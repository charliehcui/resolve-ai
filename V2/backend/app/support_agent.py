import json
import os
from typing import Literal

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langsmith import traceable
from pydantic import BaseModel, Field

from backend.app.config import PROJECT_ROOT
from backend.app.customer_agent import get_token_usage
from backend.app.handoff import SupportHandoffRecord
from backend.app.models import create_google_model
from backend.app.support_evidence import EvidenceRecord
from backend.app.support_tools import READ_TOOL_SCHEMAS

MAX_TOOL_CALLS = int(os.getenv("SUPPORT_MAX_TOOL_CALLS", "6"))
MAX_INVESTIGATION_MS = int(os.getenv("SUPPORT_MAX_INVESTIGATION_MS", "20000"))
MAX_CONSECUTIVE_ERRORS = int(os.getenv("SUPPORT_MAX_CONSECUTIVE_ERRORS", "2"))
MAX_TOOL_ERRORS = int(os.getenv("SUPPORT_MAX_TOOL_ERRORS", "3"))


class ClaimWithEvidence(BaseModel):  # 一个结论，以及支持这个结论的证据
    text: str
    evidence_ids: list[str]


class InvestigationComplete(BaseModel):  # 当前证据已经足够，可以结束自动调查
    summary: str
    confirmed_facts: list[ClaimWithEvidence]
    possible_causes: list[ClaimWithEvidence] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


class MissingInformationRequest(BaseModel):  # 缺少必要信息，需要向用户补问
    missing_fields: list[Literal["shop_id", "order_id", "sku"]]
    customer_message: str


class HumanSupportRequired(BaseModel):  # 自动调查无法继续，需要人工处理
    reason: str
    known_facts: list[ClaimWithEvidence] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


class SupportNextStep(BaseModel):  # Support Agent 决定下一步做什么
    next_step: Literal["use_tool", "request_information", "finish", "human_support"]
    tool_calls: list[dict[str, object]] = Field(default_factory=list)
    missing_information: MissingInformationRequest | None = None
    investigation_complete: InvestigationComplete | None = None
    human_support: HumanSupportRequired | None = None


class SupportAgentResult(BaseModel):  # Support Agent 最终返回结果
    answer: str
    status: Literal["needs_info", "diagnosed", "pending_human"]
    case_id: str
    evidence_ids: list[str]
    tool_path: list[str]
    tool_call_count: int
    usage: dict[str, int | None]
    trace_id: str | None = None
    ticket_id: str | None = None


def get_support_prompt_path():
    return PROJECT_ROOT / "backend" / "prompts" / "support.md"


def call_support_model(messages: list[BaseMessage]) -> BaseMessage:
    model = create_google_model(max_retries=2)
    model_with_tools = model.bind_tools(READ_TOOL_SCHEMAS)
    return model_with_tools.invoke(messages)


@traceable(name="support_next_step", run_type="llm")
def decide_support_next_step(question: str, handoff: SupportHandoffRecord, evidence: list[EvidenceRecord], remaining_calls: int) -> tuple[SupportNextStep, dict[str, int | None]]:
    prompt = get_support_prompt_path().read_text(encoding="utf-8")

    evidence_items: list[dict[str, object]] = []

    for record in evidence:
        evidence_items.append(record.model_dump())

    handoff_text = json.dumps(handoff.model_dump(), ensure_ascii=False)
    evidence_text = json.dumps(evidence_items, ensure_ascii=False, default=str)
    terminal_schemas = {
        "request_information": MissingInformationRequest.model_json_schema(),
        "finish": InvestigationComplete.model_json_schema(),
        "human_support": HumanSupportRequired.model_json_schema(),
    }
    terminal_schemas_text = json.dumps(terminal_schemas, ensure_ascii=False)

    message = f"""Handoff:
{handoff_text}

Current user message:
{question}

Evidence:
{evidence_text}

Remaining tool budget:
{remaining_calls}

Choose exactly one next step.

When more evidence is required, call one or more bound read-only query tools. Do not describe a tool call in text.

When no query tool is required, return only one JSON object in one of these forms:
- {{"next_step": "request_information", "missing_information": {{...}}}}
- {{"next_step": "finish", "investigation_complete": {{...}}}}
- {{"next_step": "human_support", "human_support": {{...}}}}

Terminal data schemas:
{terminal_schemas_text}
"""

    messages = [
        SystemMessage(content=prompt),
        HumanMessage(content=message),
    ]

    response = call_support_model(messages)
    usage = get_token_usage(response)
    response_tool_calls = getattr(response, "tool_calls", [])

    if len(response_tool_calls) > 0:
        tool_calls: list[dict[str, object]] = []

        for tool_call in response_tool_calls:
            tool_calls.append({
                "name": tool_call["name"],
                "args": tool_call.get("args") or {},
                "id": tool_call.get("id"),
            })

        return SupportNextStep(next_step="use_tool", tool_calls=tool_calls), usage

    if isinstance(response.content, str) is False:
        raise RuntimeError("Support Agent did not return valid terminal JSON")

    terminal_data = json.loads(response.content)
    next_step = SupportNextStep.model_validate(terminal_data)

    return next_step, usage


def has_shipment_evidence_conflict(evidence: list[EvidenceRecord]) -> bool:
    shipment_records: list[EvidenceRecord] = []

    for record in evidence:
        if record.status == "success" and record.object_type == "shipment":
            shipment_records.append(record)

    values: dict[str, set[str]] = {
        "shipment_id": set(),
        "carrier": set(),
        "tracking_number": set(),
    }

    for record in shipment_records:
        response = record.response
        record_values = {
            "shipment_id": response.get("shipment_id"),
            "carrier": response.get("carrier") or response.get("event_carrier"),
            "tracking_number": response.get("tracking_number") or response.get("event_tracking_number"),
        }

        for field_name, field_value in record_values.items():
            if field_value is None:
                continue

            values[field_name].add(str(field_value))

    for field_values in values.values():
        if len(field_values) > 1:
            return True

    return False


def filter_supported_claims(claims: list[ClaimWithEvidence], valid_evidence_ids: set[str]) -> list[ClaimWithEvidence]:
    supported_claims: list[ClaimWithEvidence] = []

    for claim in claims:
        if len(claim.evidence_ids) == 0:
            continue

        claim_evidence_ids = set(claim.evidence_ids)

        if claim_evidence_ids.issubset(valid_evidence_ids):
            supported_claims.append(claim)

    return supported_claims


def add_claims_to_answer(lines: list[str], claims: list[ClaimWithEvidence]) -> None:
    for claim in claims:
        evidence_text = ", ".join(claim.evidence_ids)
        lines.append(f"- {claim.text} [{evidence_text}]")


def build_human_support_answer(decision: HumanSupportRequired) -> tuple[str, Literal["pending_human"]]:
    unknowns = "；".join(decision.unknowns)

    if unknowns == "":
        unknowns = "需要进一步检查"

    answer = f"当前自动调查无法继续，需要人工处理：{decision.reason}\n尚未确认：{unknowns}"

    return answer, "pending_human"


def build_investigation_answer(decision: InvestigationComplete, evidence: list[EvidenceRecord]) -> tuple[str, Literal["diagnosed", "pending_human"]]:
    if has_shipment_evidence_conflict(evidence) is True:
        return "仓库、管理软件或平台的发货证据存在矛盾，需要人工进一步确认。", "pending_human"

    valid_evidence_ids: set[str] = set()

    for record in evidence:
        valid_evidence_ids.add(record.evidence_id)

    confirmed_claims = filter_supported_claims(decision.confirmed_facts, valid_evidence_ids)
    possible_causes = filter_supported_claims(decision.possible_causes, valid_evidence_ids)

    if len(confirmed_claims) == 0:
        return "当前证据不足以形成可靠结论，需要人工进一步处理。", "pending_human"

    lines = [
        decision.summary,
        "已确认事实：",
    ]

    add_claims_to_answer(lines, confirmed_claims)

    if len(possible_causes) > 0:
        lines.append("可能原因：")
        add_claims_to_answer(lines, possible_causes)

    if len(decision.unknowns) > 0:
        lines.append("尚未确认：")

        for unknown in decision.unknowns:
            lines.append(f"- {unknown}")

    return "\n".join(lines), "diagnosed"


def build_support_answer(next_step: SupportNextStep, evidence: list[EvidenceRecord]) -> tuple[str, Literal["needs_info", "diagnosed", "pending_human"]]:
    if next_step.next_step == "request_information":
        if next_step.missing_information is None:
            return "还需要补充必要的订单、店铺或商品信息。", "needs_info"

        return next_step.missing_information.customer_message, "needs_info"

    if next_step.next_step == "human_support":
        if next_step.human_support is None:
            return "当前自动调查无法继续，需要人工进一步处理。", "pending_human"

        return build_human_support_answer(next_step.human_support)

    if next_step.investigation_complete is None:
        return "当前证据不足以形成可靠结论，需要人工进一步处理。", "pending_human"

    return build_investigation_answer(next_step.investigation_complete, evidence)



# Handoff + Evidence + 用户消息
# ↓
# decide_support_next_step()
# ↓
# 如果需要查数据
# → 调工具
# → 得到新 Evidence
# → 再决定一次

# 如果信息不足
# → 问用户

# 如果证据足够
# → 生成最终答案

# 如果自动调查不能继续
# → 转人工


# Handoff
# +
# Evidence
# +
# 用户最新消息
#         ↓
# decide_support_next_step()
#         ↓
#    SupportNextStep
#         ↓
#  ┌──────┼──────────┬───────────┐
#  │      │          │           │
# use   request     finish      human
# tool  information              support
#  │      │          │           │
#  ↓      ↓          ↓           ↓
# 查询   问用户     生成结果      人工
#  │
#  ↓
# Evidence
#  │
#  └────────→ 再次 decide_support_next_step()