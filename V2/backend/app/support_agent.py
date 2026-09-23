import json
import os
from typing import Literal

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langsmith import traceable
from pydantic import BaseModel, Field

from backend.app.config import PROJECT_ROOT, get_settings
from backend.app.customer_agent import get_token_usage
from backend.app.handoff import SupportHandoff
from backend.app.models import create_google_model
from backend.app.support_evidence import EvidenceRecord
from backend.app.support_tools import READ_TOOL_SCHEMAS

MAX_TOOL_CALLS = int(os.getenv("SUPPORT_MAX_TOOL_CALLS", "6"))
MAX_INVESTIGATION_MS = int(os.getenv("SUPPORT_MAX_INVESTIGATION_MS", "20000"))
MAX_CONSECUTIVE_ERRORS = int(os.getenv("SUPPORT_MAX_CONSECUTIVE_ERRORS", "2"))
MAX_TOOL_ERRORS = int(os.getenv("SUPPORT_MAX_TOOL_ERRORS", "3"))


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

    missing_fields: list[Literal["shop_id", "order_id", "sku"]]
    customer_message: str


class EscalateInvestigation(BaseModel):
    """Stop safely when evidence is insufficient, conflicting, unavailable, or over budget."""

    reason: str
    known_facts: list[EvidenceClaim] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


class SupportInvestigationResult(BaseModel):
    answer: str
    status: Literal["needs_info", "diagnosed", "pending_human"]
    case_id: str
    evidence_ids: list[str]
    tool_path: list[str]
    tool_call_count: int
    models_used: list[str] = Field(default_factory=list)
    usage: dict[str, int | None]
    trace_id: str | None = None
    ticket_id: str | None = None


CONTROL_SCHEMAS = [FinishInvestigation, RequestInformation, EscalateInvestigation]
SUPPORT_SCHEMAS = [*READ_TOOL_SCHEMAS, *CONTROL_SCHEMAS]


def is_temporary_google_error(error: Exception) -> bool:
    code = getattr(error, "code", None)
    status = str(getattr(error, "status", "") or "").upper()
    message = str(error).lower()

    if code == 503:
        return True

    if status == "UNAVAILABLE":
        return True

    mentions_temporary_outage = "unavailable" in message or "high demand" in message
    return "503" in message and mentions_temporary_outage


def invoke_support_model(model_name: str, messages: list[BaseMessage]):
    model = create_google_model(model_name=model_name, max_retries=1)
    model_with_tools = model.bind_tools(SUPPORT_SCHEMAS, tool_choice="any")
    return model_with_tools.invoke(messages)


@traceable(name="support_next_step", run_type="llm")
def plan_support_step(question: str, handoff: SupportHandoff, evidence: list[EvidenceRecord], remaining_calls: int) -> tuple[list[dict[str, object]], dict[str, int | None], str]:
    prompt = handoff_path().read_text(encoding="utf-8")

    evidence_items: list[dict[str, object]] = []
    for record in evidence:
        evidence_items.append(record.model_dump())

    evidence_text = json.dumps(evidence_items, ensure_ascii=False, default=str)
    handoff_text = json.dumps(handoff.model_dump(), ensure_ascii=False)
    message = f"Handoff:\n{handoff_text}\n\nCurrent user message:\n{question}\n\nEvidence:\n{evidence_text}\n\nRemaining tool budget: {remaining_calls}"
    messages = [SystemMessage(content=prompt), HumanMessage(content=message)]

    settings = get_settings()
    model_name = settings.google_model

    try:
        response = invoke_support_model(model_name, messages)
    except Exception as primary_error:
        primary_error.attempted_models = [settings.google_model]
        if not is_temporary_google_error(primary_error) or settings.google_fallback_model == settings.google_model:
            raise

        model_name = settings.google_fallback_model
        try:
            response = invoke_support_model(model_name, messages)
        except Exception as fallback_error:
            fallback_error.attempted_models = [settings.google_model, settings.google_fallback_model]
            raise

    calls: list[dict[str, object]] = []
    for tool_call in response.tool_calls:
        call = {
            "name": tool_call["name"],
            "args": tool_call.get("args") or {},
            "id": tool_call.get("id"),
        }
        calls.append(call)

    return calls, get_token_usage(response), model_name


def handoff_path():
    return PROJECT_ROOT / "backend" / "prompts" / "support.md"


def shipment_evidence_conflicts(evidence: list[EvidenceRecord]) -> bool:
    shipment_records: list[EvidenceRecord] = []
    for record in evidence:
        if record.status == "success" and record.object_type == "shipment":
            shipment_records.append(record)

    values: dict[str, set[str]] = {"shipment_id": set(), "carrier": set(), "tracking_number": set()}
    for record in shipment_records:
        response = record.response
        candidates = {
            "shipment_id": response.get("shipment_id"),
            "carrier": response.get("carrier") or response.get("event_carrier"),
            "tracking_number": response.get("tracking_number") or response.get("event_tracking_number"),
        }
        for field, value in candidates.items():
            if value:
                values[field].add(str(value))
    for field_values in values.values():
        if len(field_values) > 1:
            return True

    return False


def supported_claims(claims: list[EvidenceClaim], valid_evidence_ids: set[str]) -> list[EvidenceClaim]:
    supported: list[EvidenceClaim] = []

    for claim in claims:
        if not claim.evidence_ids:
            continue

        claim_evidence_ids = set(claim.evidence_ids)
        if claim_evidence_ids.issubset(valid_evidence_ids):
            supported.append(claim)

    return supported


def append_claims(lines: list[str], claims: list[EvidenceClaim]) -> None:
    for claim in claims:
        evidence_text = ", ".join(claim.evidence_ids)
        lines.append(f"- {claim.text} [{evidence_text}]")


def render_escalation_result(args: dict[str, object]) -> tuple[str, Literal["pending_human"]]:
    decision = EscalateInvestigation(**args)
    unknowns = "；".join(decision.unknowns)
    if not unknowns:
        unknowns = "需要进一步检查"

    answer = f"当前调查需要人工继续处理：{decision.reason}\n尚未确认：{unknowns}"
    return answer, "pending_human"


def render_finished_investigation(args: dict[str, object], evidence: list[EvidenceRecord]) -> tuple[str, Literal["diagnosed", "pending_human"]]:
    if shipment_evidence_conflicts(evidence):
        return "仓库、管理软件或平台的发货证据存在矛盾，需要刷新事实后再确认根因。", "pending_human"

    decision = FinishInvestigation(**args)
    valid_evidence_ids: set[str] = set()
    for record in evidence:
        valid_evidence_ids.add(record.evidence_id)

    confirmed_claims = supported_claims(decision.confirmed_facts, valid_evidence_ids)
    possible_causes = supported_claims(decision.possible_causes, valid_evidence_ids)

    if not confirmed_claims:
        return "当前证据不足以形成可核验结论，已保留为待人工处理。", "pending_human"

    lines = [decision.summary, "已确认事实："]
    append_claims(lines, confirmed_claims)

    if possible_causes:
        lines.append("可能原因：")
        append_claims(lines, possible_causes)

    if decision.unknowns:
        lines.append("尚未确认：")
        for unknown in decision.unknowns:
            lines.append(f"- {unknown}")

    return "\n".join(lines), "diagnosed"


def render_control_result(tool_call: dict[str, object], evidence: list[EvidenceRecord]) -> tuple[str, Literal["needs_info", "diagnosed", "pending_human"]]:
    name = str(tool_call.get("name", ""))
    args = dict(tool_call.get("args") or {})

    if name == "RequestInformation":
        decision = RequestInformation(**args)
        return decision.customer_message, "needs_info"

    if name == "EscalateInvestigation":
        return render_escalation_result(args)

    return render_finished_investigation(args, evidence)
