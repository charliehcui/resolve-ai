import json
import re
from typing import Literal
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage
from langsmith import traceable
from pydantic import BaseModel, Field

from app.config import get_settings
from app.customer import token_usage
from app.db import get_connection
from app.evidence import EvidenceRecord, load_evidence
from app.models import AuthContext, Citation, create_google_model
from app.tools import READ_TOOL_SCHEMAS

MAX_TOOL_CALLS = 6
MAX_INVESTIGATION_MS = 20_000
MAX_CONSECUTIVE_ERRORS = 2


class SupportHandoff(BaseModel):
    handoff_id: str
    conversation_id: str
    company_id: str
    customer_problem: str
    customer_answer: str
    citations: list[Citation] = Field(default_factory=list)
    attempted_steps: list[str] = Field(default_factory=list)
    known_shop_id: str | None = None
    known_order_id: str | None = None
    unresolved_reason: str
    missing_fields: list[str] = Field(default_factory=list)


class EvidenceClaim(BaseModel):
    text: str
    evidence_ids: list[str]


class FinishInvestigation(BaseModel):
    """Finish when current evidence supports a useful read-only diagnosis."""

    summary: str
    confirmed_facts: list[EvidenceClaim]
    possible_causes: list[EvidenceClaim] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


class RequestInformation(BaseModel):
    """Ask for a missing shop or order identifier before using business tools."""

    missing_fields: list[Literal["shop_id", "order_id"]]
    customer_message: str


class EscalateInvestigation(BaseModel):
    """Stop safely when evidence is insufficient, conflicting, unavailable, or over budget."""

    reason: str
    known_facts: list[EvidenceClaim] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


class SupportResult(BaseModel):
    answer: str
    status: Literal["needs_info", "diagnosed", "pending_human"]
    case_id: str
    evidence_ids: list[str]
    tool_path: list[str]
    tool_call_count: int
    models_used: list[str] = Field(default_factory=list)
    usage: dict[str, int | None]
    trace_id: str | None = None


CONTROL_SCHEMAS = [FinishInvestigation, RequestInformation, EscalateInvestigation]
SUPPORT_SCHEMAS = [*READ_TOOL_SCHEMAS, *CONTROL_SCHEMAS]


def extract_identifiers(text: str) -> tuple[str | None, str | None]:
    shop_match = re.search(r"\bshop-[A-Za-z0-9-]+\b", text, re.IGNORECASE)
    order_match = re.search(r"\b(?:O|ORDER)-[A-Za-z0-9-]+\b", text, re.IGNORECASE)
    return (shop_match.group(0) if shop_match else None, order_match.group(0) if order_match else None)


def handoff_to_support(auth: AuthContext, conversation_id: str, question: str, customer_answer: str, history: list[dict[str, object]]) -> tuple[SupportHandoff, str]:
    shop_id, order_id = extract_identifiers(question)
    previous_answer = ""
    citations: list[dict[str, str]] = []
    for message in reversed(history):
        if message["role"] == "assistant":
            previous_answer = str(message["content"])
            metadata = message.get("metadata") or {}
            citations = list(metadata.get("citations") or [])
            break
    attempted_steps = [str(message["content"]) for message in history[-6:] if message["role"] == "user" and re.search(r"试过|尝试|仍然|还是|没有解决|未解决", str(message["content"]))]
    if re.search(r"试过|尝试|仍然|还是|没有解决|未解决", question):
        attempted_steps.append(question)
    missing_fields = [name for name, value in (("shop_id", shop_id), ("order_id", order_id)) if value is None]
    handoff_id = str(uuid4())
    case_id = str(uuid4())
    with get_connection() as connection:
        existing = connection.execute("""SELECT h.handoff_id::text, c.case_id::text FROM support.handoffs h JOIN support.cases c ON c.handoff_id = h.handoff_id
            WHERE h.conversation_id = %s AND h.company_id = %s""", (conversation_id, auth.company_id)).fetchone()
        if existing:
            return load_handoff(conversation_id, auth), existing["case_id"]
        conversation = connection.execute("SELECT company_id, user_id FROM support.conversations WHERE conversation_id = %s FOR UPDATE", (conversation_id,)).fetchone()
        if conversation is None or conversation["company_id"] != auth.company_id or conversation["user_id"] != auth.user_id:
            raise PermissionError("Conversation is not available in this user scope")
        connection.execute(
            """INSERT INTO support.handoffs (handoff_id, conversation_id, company_id, customer_problem, customer_answer, citations, attempted_steps, known_shop_id, known_order_id, unresolved_reason, missing_fields)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s::jsonb)""",
            (handoff_id, conversation_id, auth.company_id, question, previous_answer or customer_answer, json.dumps(citations), json.dumps(attempted_steps, ensure_ascii=False), shop_id, order_id, question, json.dumps(missing_fields)),
        )
        connection.execute("INSERT INTO support.cases (case_id, conversation_id, handoff_id, company_id) VALUES (%s, %s, %s, %s)", (case_id, conversation_id, handoff_id, auth.company_id))
        connection.execute("UPDATE support.conversations SET active_role = 'SUPPORT', updated_at = NOW() WHERE conversation_id = %s", (conversation_id,))
    return load_handoff(conversation_id, auth), case_id


def load_handoff(conversation_id: str, auth: AuthContext) -> SupportHandoff:
    with get_connection() as connection:
        row = connection.execute("""SELECT handoff_id::text, conversation_id::text, company_id, customer_problem, customer_answer, citations, attempted_steps,
            known_shop_id, known_order_id, unresolved_reason, missing_fields FROM support.handoffs
            WHERE conversation_id = %s AND company_id = %s""", (conversation_id, auth.company_id)).fetchone()
    if row is None:
        raise PermissionError("Support handoff is not available in this user scope")
    return SupportHandoff(**row)


def update_handoff_identifiers(conversation_id: str, auth: AuthContext, text: str) -> SupportHandoff:
    shop_id, order_id = extract_identifiers(text)
    with get_connection() as connection:
        row = connection.execute("SELECT known_shop_id, known_order_id FROM support.handoffs WHERE conversation_id = %s AND company_id = %s FOR UPDATE", (conversation_id, auth.company_id)).fetchone()
        if row is None:
            raise PermissionError("Support handoff is not available in this user scope")
        known_shop_id = shop_id or row["known_shop_id"]
        known_order_id = order_id or row["known_order_id"]
        missing_fields = [name for name, value in (("shop_id", known_shop_id), ("order_id", known_order_id)) if value is None]
        connection.execute("UPDATE support.handoffs SET known_shop_id = %s, known_order_id = %s, missing_fields = %s::jsonb, updated_at = NOW() WHERE conversation_id = %s", (known_shop_id, known_order_id, json.dumps(missing_fields), conversation_id))
    return load_handoff(conversation_id, auth)


def get_case_id(conversation_id: str, auth: AuthContext) -> str:
    with get_connection() as connection:
        row = connection.execute("SELECT case_id::text FROM support.cases WHERE conversation_id = %s AND company_id = %s", (conversation_id, auth.company_id)).fetchone()
    if row is None:
        raise PermissionError("Support case is not available in this user scope")
    return row["case_id"]


def update_case(case_id: str, status: str, outcome: str, tool_call_count: int, total_latency_ms: int) -> None:
    with get_connection() as connection:
        connection.execute("UPDATE support.cases SET status = %s, outcome = %s, tool_call_count = %s, total_latency_ms = %s, updated_at = NOW() WHERE case_id = %s", (status, outcome, tool_call_count, total_latency_ms, case_id))


def show_case(case_id: str, auth: AuthContext) -> dict[str, object]:
    with get_connection() as connection:
        case = connection.execute("""SELECT c.case_id::text, c.conversation_id::text, c.status, c.outcome, c.tool_call_count, c.total_latency_ms,
            h.customer_problem, h.customer_answer, h.citations, h.attempted_steps, h.known_shop_id, h.known_order_id, h.missing_fields
            FROM support.cases c JOIN support.handoffs h ON h.handoff_id = c.handoff_id JOIN support.conversations v ON v.conversation_id = c.conversation_id
            WHERE c.case_id = %s AND c.company_id = %s AND v.user_id = %s""", (case_id, auth.company_id, auth.user_id)).fetchone()
    if case is None:
        raise PermissionError("Support case is not available in this user scope")
    result = dict(case)
    result["evidence"] = [record.model_dump() for record in load_evidence(case_id)]
    return result


def is_temporary_google_error(error: Exception) -> bool:
    code = getattr(error, "code", None)
    status = str(getattr(error, "status", "") or "").upper()
    message = str(error).lower()
    return code == 503 or status == "UNAVAILABLE" or ("503" in message and ("unavailable" in message or "high demand" in message))


@traceable(name="support_next_step", run_type="llm")
def plan_support_step(question: str, handoff: SupportHandoff, evidence: list[EvidenceRecord], remaining_calls: int) -> tuple[list[dict[str, object]], dict[str, int | None], str]:
    prompt = (handoff_path()).read_text(encoding="utf-8")
    evidence_text = json.dumps([record.model_dump() for record in evidence], ensure_ascii=False, default=str)
    handoff_text = json.dumps(handoff.model_dump(), ensure_ascii=False)
    message = f"Handoff:\n{handoff_text}\n\nCurrent user message:\n{question}\n\nEvidence:\n{evidence_text}\n\nRemaining tool budget: {remaining_calls}"
    settings = get_settings()
    model_name = settings.google_model
    try:
        response = create_google_model(model_name=model_name, max_retries=1).bind_tools(SUPPORT_SCHEMAS, tool_choice="any").invoke([SystemMessage(content=prompt), HumanMessage(content=message)])
    except Exception as primary_error:
        primary_error.attempted_models = [settings.google_model]
        if not is_temporary_google_error(primary_error) or settings.google_fallback_model == settings.google_model:
            raise
        model_name = settings.google_fallback_model
        try:
            response = create_google_model(model_name=model_name, max_retries=1).bind_tools(SUPPORT_SCHEMAS, tool_choice="any").invoke([SystemMessage(content=prompt), HumanMessage(content=message)])
        except Exception as fallback_error:
            fallback_error.attempted_models = [settings.google_model, settings.google_fallback_model]
            raise
    calls = [{"name": call["name"], "args": call.get("args") or {}, "id": call.get("id")} for call in response.tool_calls]
    return calls, token_usage(response), model_name


def handoff_path():
    from app.config import PROJECT_ROOT

    return PROJECT_ROOT / "prompts" / "support.md"


def render_control_result(tool_call: dict[str, object], evidence: list[EvidenceRecord]) -> tuple[str, Literal["needs_info", "diagnosed", "pending_human"]]:
    name = str(tool_call.get("name", ""))
    args = dict(tool_call.get("args") or {})
    if name == "RequestInformation":
        decision = RequestInformation(**args)
        return decision.customer_message, "needs_info"
    if name == "EscalateInvestigation":
        decision = EscalateInvestigation(**args)
        return f"当前调查需要人工继续处理：{decision.reason}\n尚未确认：{'；'.join(decision.unknowns) or '需要进一步检查'}", "pending_human"
    decision = FinishInvestigation(**args)
    valid_ids = {record.evidence_id for record in evidence}
    confirmed = [claim for claim in decision.confirmed_facts if claim.evidence_ids and set(claim.evidence_ids).issubset(valid_ids)]
    possible = [claim for claim in decision.possible_causes if claim.evidence_ids and set(claim.evidence_ids).issubset(valid_ids)]
    if not confirmed:
        return "当前证据不足以形成可核验结论，已保留为待人工处理。", "pending_human"
    lines = [decision.summary, "已确认事实："]
    lines.extend(f"- {claim.text} [{', '.join(claim.evidence_ids)}]" for claim in confirmed)
    if possible:
        lines.append("可能原因：")
        lines.extend(f"- {claim.text} [{', '.join(claim.evidence_ids)}]" for claim in possible)
    if decision.unknowns:
        lines.append("尚未确认：")
        lines.extend(f"- {item}" for item in decision.unknowns)
    return "\n".join(lines), "diagnosed"
