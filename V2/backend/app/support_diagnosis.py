import json
import os
import re
from typing import Literal

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langsmith import traceable
from pydantic import BaseModel, Field

from backend.app.config import PROJECT_ROOT
from backend.app.customer_agent import get_token_usage
from backend.app.handoff import SupportHandoffRecord
from backend.app.llm import StructuredOutputError, parse_structured_output
from backend.app.models import create_model
from backend.app.support_evidence import EvidenceRecord, is_read_validation_error
from backend.app.support_tools import READ_TOOL_SCHEMAS

MAX_TOOL_CALLS = int(os.getenv("SUPPORT_MAX_TOOL_CALLS", "6"))
MAX_INVESTIGATION_MS = int(os.getenv("SUPPORT_MAX_INVESTIGATION_MS", "20000"))
MAX_CONSECUTIVE_ERRORS = int(os.getenv("SUPPORT_MAX_CONSECUTIVE_ERRORS", "2"))
MAX_TOOL_ERRORS = int(os.getenv("SUPPORT_MAX_TOOL_ERRORS", "3"))


class ClaimWithEvidence(BaseModel):  # 一个结论，以及支持这个结论的证据
    text: str
    evidence_ids: list[str]


class CandidateAction(BaseModel):
    model_config = {"extra": "forbid"}
    action_type: str
    reason: str
    evidence_ids: list[str]


class InvestigationComplete(BaseModel):  # 当前证据已经足够，可以结束自动调查
    summary: str
    confirmed_facts: list[ClaimWithEvidence]
    possible_causes: list[ClaimWithEvidence] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    recommended_action: CandidateAction | None = None
    outcome: Literal["diagnosed", "retry_later", "user_action_required"] = "diagnosed"


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
    status: str
    case_id: str
    evidence_ids: list[str]
    tool_path: list[str]
    tool_call_count: int
    usage: dict[str, int | None]
    trace_id: str | None = None
    ticket_id: str | None = None
    action_plan_id: str | None = None
    action_plan: dict[str, object] | None = None


def get_support_prompt_path():
    return PROJECT_ROOT / "backend" / "prompts" / "support.md"


def call_support_model(messages: list[BaseMessage], terminal_only: bool = False, tools: list[type[BaseModel]] | None = None) -> BaseMessage:
    model = create_model(max_retries=0)
    if terminal_only:
        return model.invoke(messages, response_format={"type": "json_object"})
    model_with_tools = model.bind_tools(READ_TOOL_SCHEMAS if tools is None else tools)
    return model_with_tools.invoke(messages)


@traceable(name="support_next_step", run_type="llm")
def decide_support_next_step(question: str, handoff: SupportHandoffRecord, evidence: list[EvidenceRecord], remaining_calls: int) -> tuple[SupportNextStep, dict[str, int | None]]:
    prompt = get_support_prompt_path().read_text(encoding="utf-8")

    evidence_items: list[dict[str, object]] = []
    valid_evidence_ids: set[str] = set()
    validation_errors: list[str] = []

    for record in evidence:
        item = record.model_dump()
        if is_read_validation_error(record):
            item["evidence_kind"] = "argument_validation_error"
            validation_errors.append(f"工具参数校验失败，后台业务状态未查询：{record.response.get('error_code')} [{record.evidence_id}]")
        else:
            item["evidence_kind"] = "backend_response" if record.status in {"success", "empty", "not_found"} else "backend_read_error"
            valid_evidence_ids.add(record.evidence_id)
        evidence_items.append(item)

    identifiers = {"shop_id": handoff.known_shop_id, "order_id": handoff.known_order_id, "sku": handoff.known_sku}
    available_tools = []
    for schema in READ_TOOL_SCHEMAS:
        if all(identifiers.get(field) for field in schema.model_fields):
            arguments = {field: identifiers[field] for field in schema.model_fields}
            already_queried = False
            for record in evidence:
                if record.tool_name == schema.__name__ and record.request == arguments and not is_read_validation_error(record):
                    already_queried = True
                    break
            if already_queried:
                continue
            available_tools.append(schema)

    primary_tools = primary_read_tools(handoff, question)
    attempted = {record.tool_name for record in evidence if record.source_service != "support" and record.request.get("shop_id") == handoff.known_shop_id and ("order_id" not in record.request or record.request["order_id"] == handoff.known_order_id) and ("sku" not in record.request or record.request["sku"] == handoff.known_sku)}
    primary_missing = primary_tools - attempted
    prioritized = [schema for schema in available_tools if schema.__name__ in primary_missing]
    if prioritized:
        available_tools = prioritized

    handoff_text = json.dumps(handoff.model_dump(), ensure_ascii=False)
    evidence_text = json.dumps(evidence_items, ensure_ascii=False, default=str)
    terminal_schemas = {
        "request_information": MissingInformationRequest.model_json_schema(),
        "finish": InvestigationComplete.model_json_schema(),
        "human_support": HumanSupportRequired.model_json_schema(),
    }
    terminal_schemas_text = json.dumps(terminal_schemas, ensure_ascii=False)
    from backend.app.support_action_registry import ACTION_REGISTRY
    candidate_actions = ", ".join(ACTION_REGISTRY)

    message = f"""Handoff:
{handoff_text}

Current user message:
{question}

Evidence:
{evidence_text}

Remaining tool budget:
{remaining_calls}

Primary fact queries for this request: {', '.join(sorted(primary_tools))}.
Read the unqueried primary facts together when independent before spending budget on auxiliary queries.

Choose exactly one next step.
The finish response may include recommended_action with action_type, reason and real evidence_ids.
Supported Candidate Actions: {candidate_actions}.
Return diagnosis and recommendation in this SAME response. Never specify risk, permission or approval policy.

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

    response = call_support_model(messages, terminal_only=remaining_calls == 0 or not available_tools, tools=available_tools)
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

    next_step = parse_structured_output(response.content, SupportNextStep)
    payload_name = {"finish": "investigation_complete", "request_information": "missing_information", "human_support": "human_support"}.get(next_step.next_step)
    if payload_name and getattr(next_step, payload_name) is None:
        raise StructuredOutputError("Missing terminal JSON payload: " + payload_name)

    if next_step.investigation_complete is not None:
        decision = next_step.investigation_complete
        decision.confirmed_facts = filter_supported_claims(decision.confirmed_facts, valid_evidence_ids)
        decision.possible_causes = filter_supported_claims(decision.possible_causes, valid_evidence_ids)
        decision.unknowns.extend(validation_errors)
    if next_step.human_support is not None:
        decision = next_step.human_support
        decision.known_facts = filter_supported_claims(decision.known_facts, valid_evidence_ids)
        decision.unknowns.extend(validation_errors)

    return next_step, usage


def primary_read_tools(handoff: SupportHandoffRecord, question: str) -> set[str]:
    if handoff.known_sku:
        tools = {"GetStockStatus"}
    elif re.search(r"发货|出库|运单|物流|shipment|shipping|tracking", question, flags=re.IGNORECASE):
        tools = {"GetWarehouseShipment", "GetShipmentProcessRecords", "GetPlatformShipment"}
    else:
        tools = {"GetOrder", "GetOrderProcessRecords"} if handoff.known_order_id else set()
    if re.search(r"连接|授权|connection|authorization", question, flags=re.IGNORECASE):
        tools.add("GetShopConnectionStatus")
    if re.search(r"同步开关|同步配置|当前同步|当前设置|当前查询|sync settings?", question, flags=re.IGNORECASE):
        tools.add("GetShopSyncStatus")
    return tools


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


def direct_cause_values(value: object) -> list[str]:
    values = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"cause", "root_cause", "failure_reason", "reason", "error_message"} and isinstance(item, str):
                values.append(item)
            elif isinstance(item, (dict, list)):
                values.extend(direct_cause_values(item))
    elif isinstance(value, list):
        for item in value:
            values.extend(direct_cause_values(item))
    return values


def ground_cause_text(text: str, evidence: list[EvidenceRecord]) -> str:
    # 只拦截常见的具体根因和猜测表述，不从错误码推导根因。
    pattern = r"网络(?:连接)?(?:故障|异常|问题)|(?:平台|系统)(?:正在)?(?:维护|内部故障|故障)|(?:商品|产品)(?:尚)?未创建|(?:映射)?配置(?:尚)?未完成|资源(?:问题|不足|耗尽)|network (?:fault|failure|issue|problem)|platform maintenance|system (?:fault|failure)|resource (?:problem|shortage|exhaustion)|(?:product|item) (?:not|never) created|configuration (?:incomplete|not completed)"
    causes = []
    for record in evidence:
        if record.status == "success" and not is_read_validation_error(record):
            causes.extend(direct_cause_values(record.response))
    for match in re.finditer(pattern, text, flags=re.IGNORECASE):
        phrase = match.group().casefold()
        if not any(phrase in cause.casefold() for cause in causes):
            return "具体原因目前无法确认"
    if re.search(r"可能(?:由|是|因为|由于)|疑似|猜测|\b(?:might be|could be|possibly)\b", text, flags=re.IGNORECASE):
        return "具体原因目前无法确认"
    causal = re.search(r"(?:原因|根因)(?:是|为)|由于|因为|导致|造成|引起|未发布到|\b(?:because|due to|caused by|reason is)\b", text, flags=re.IGNORECASE)
    if causal:
        cause = text[causal.end():].strip(" 。.;；")
        if text.strip(" 。.;；") not in causes and cause not in causes:
            return "具体原因目前无法确认"
    return text


def normalize_unknown_causes(unknowns: list[str]) -> list[str]:
    result = []
    for text in unknowns:
        if re.search(r"原因|根因|可能|疑似|猜测|cause|might|could be", text, flags=re.IGNORECASE) or ground_cause_text(text, []) != text:
            text = "具体原因目前无法确认"
        if text not in result:
            result.append(text)
    return result


def build_human_support_answer(decision: HumanSupportRequired, evidence: list[EvidenceRecord] | None = None) -> tuple[str, Literal["pending_human"]]:
    decision.reason = ground_cause_text(decision.reason, evidence or [])
    decision.unknowns = normalize_unknown_causes(decision.unknowns)
    unknowns = "；".join(decision.unknowns)

    if unknowns == "":
        unknowns = "需要进一步检查"

    answer = f"当前自动调查无法继续，需要人工处理：{decision.reason}\n尚未确认：{unknowns}"
    facts = []
    for claim in decision.known_facts:
        cited = [record for record in evidence or [] if record.evidence_id in claim.evidence_ids and not is_read_validation_error(record)]
        if cited and set(claim.evidence_ids) == {record.evidence_id for record in cited} and ground_cause_text(claim.text, cited) == claim.text:
            facts.append(claim)
    if facts:
        lines = [answer, "已确认事实："]
        add_claims_to_answer(lines, facts)
        answer = "\n".join(lines)

    return answer, "pending_human"


def build_investigation_answer(decision: InvestigationComplete, evidence: list[EvidenceRecord]) -> tuple[str, str]:
    if has_shipment_evidence_conflict(evidence) is True:
        return "仓库、管理软件或平台的发货证据存在矛盾，需要人工进一步确认。", "pending_human"

    valid_evidence_ids: set[str] = set()

    for record in evidence:
        if not is_read_validation_error(record):
            valid_evidence_ids.add(record.evidence_id)

    confirmed_claims = []
    for claim in filter_supported_claims(decision.confirmed_facts, valid_evidence_ids):
        cited = [record for record in evidence if record.evidence_id in claim.evidence_ids]
        if ground_cause_text(claim.text, cited) == claim.text:
            confirmed_claims.append(claim)
        else:
            decision.unknowns.append("具体原因目前无法确认")
    possible_causes = []
    for claim in filter_supported_claims(decision.possible_causes, valid_evidence_ids):
        causes = []
        for record in evidence:
            if record.evidence_id in claim.evidence_ids and record.status == "success" and not is_read_validation_error(record):
                causes.extend(direct_cause_values(record.response))
        if claim.text.strip() in causes:
            possible_causes.append(claim)
        else:
            decision.unknowns.append("具体原因目前无法确认")
    decision.summary = ground_cause_text(decision.summary, evidence)
    decision.unknowns = normalize_unknown_causes(decision.unknowns)
    if decision.recommended_action is not None:
        decision.recommended_action.reason = ground_cause_text(decision.recommended_action.reason, evidence)

    if len(confirmed_claims) == 0:
        return "当前证据不足以形成可靠结论，需要人工进一步处理。", "pending_human"

    lines = [
        decision.summary,
        "已确认事实：",
    ]

    add_claims_to_answer(lines, confirmed_claims)

    if len(possible_causes) > 0:
        lines.append("有直接证据的原因：")
        add_claims_to_answer(lines, possible_causes)

    if len(decision.unknowns) > 0:
        lines.append("尚未确认：")

        for unknown in decision.unknowns:
            lines.append(f"- {unknown}")

    return "\n".join(lines), decision.outcome


def build_support_answer(next_step: SupportNextStep, evidence: list[EvidenceRecord]) -> tuple[str, str]:
    if next_step.next_step == "request_information":
        if next_step.missing_information is None:
            return "还需要补充必要的订单、店铺或商品信息。", "needs_info"

        return next_step.missing_information.customer_message, "needs_info"

    if next_step.next_step == "human_support":
        if next_step.human_support is None:
            return "当前自动调查无法继续，需要人工进一步处理。", "pending_human"

        return build_human_support_answer(next_step.human_support, evidence)

    if next_step.investigation_complete is None:
        return "当前证据不足以形成可靠结论，需要人工进一步处理。", "pending_human"

    return build_investigation_answer(next_step.investigation_complete, evidence)



# support_diagnosis.py
# Support Agent 的“判断和诊断层”
#
# 1. 让 LLM 决定下一步做什么
# 2. 定义 Agent 返回的数据格式
# 3. 调查完成后，检查 Diagnosis 是否有 Evidence 支持
#
#
# decide_support_next_step(question, handoff, evidence, remaining_calls)
# 【Diagnosis Agent 核心入口：根据当前问题和已有证据决定下一步】
# ↓
#
# get_support_prompt_path()
# 【读取 Support Agent 的 System Prompt】
# ↓
#
# 准备给 LLM 的输入
# │
# ├── Handoff
# │   【之前已经知道的问题背景】
# │
# ├── Current User Message
# │   【用户最新说了什么】
# │
# ├── Evidence
# │   【目前已经查到的事实】
# │
# ├── Remaining Tool Budget
# │   【还允许继续查询多少次】
# │
# ├── ACTION_REGISTRY
# │   【告诉 LLM 当前有哪些 Action 可以推荐】
# │
# └── Terminal Schemas
#     【告诉 LLM 最终结果必须按照什么格式返回】
#
# ↓
#
# call_support_model()
# 【真正调用 LLM】
# ↓
#
# model.bind_tools(READ_TOOL_SCHEMAS)
# 【告诉 LLM 当前有哪些 Read Tools 可以申请调用】
# ↓
#
# LLM Response
# ↓
#
# ├── 有 Tool Calls
# │   【LLM 认为证据还不够，需要继续查后台找原因】
# │
# │   例如：
# │   GetOrder(order_id="123")
# │
# │   注意：
# │   Tool Call = LLM “申请调用这个工具”
# │   这里还没有真正执行 Tool
# │
# │      ↓
# │
# │   SupportNextStep    可以是：1. "use_tool"  2. "request_information"  3. "finish"  4. "human_support"
# │   【Data Model：统一记录 Agent 下一步决定】
# │
# │   next_step = "use_tool"
# │   tool_calls = [...]
# │
# │   【意思：下一步继续调用这些 Read Tools 获取新的 Evidence】
# │
# │
# └── 没有 Tool Calls
#     【LLM 认为现在不需要继续查询后台，可以直接做下一步决定】
#        ↓
#
#     LLM 返回 JSON
#     【直接说明：问用户 / 完成诊断 / 转人工】
#        ↓
#
#     SupportNextStep
#     【Data Model：统一记录 Agent 下一步决定】
#        ↓
#
#        ├── next_step = "request_information"
#        │      ↓
#        │   MissingInformationRequest
#        │   【Data Model：记录缺什么信息，以及应该怎么问用户】
#        │
#        │
#        ├── next_step = "finish"
#        │      ↓
#        │   InvestigationComplete
#        │   【Data Model：完整 Diagnosis 结果】
#        │
#        │   summary / confirmed_facts / possible_causes /
#        │   unknowns / outcome / recommended_action
#        │
#        │      ↓
#        │   recommended_action 有值？
#        │
#        │   ├── No
#        │   │   【只给出 Diagnosis，不建议自动操作】
#        │   │
#        │   └── Yes
#        │       ↓
#        │     CandidateAction
#        │     【Data Model：LLM 建议下一步执行什么 Action】
#        │
#        │     action_type / reason / evidence_ids
#        │
#        │
#        └── next_step = "human_support"
#               ↓
#            HumanSupportRequired
#            【Data Model：记录为什么自动调查无法继续，需要人工】
#
# ↓
#
# 返回 SupportNextStep + Token Usage
# 【这次 Diagnosis Agent 的决定完成】



#检查这个 Diagnosis 靠不靠谱，并整理成回复
# build_support_answer(next_step, evidence)
# 【根据 Agent 最终决定，生成对应结果】
# ↓
#
# ├── request_information
# │      ↓
# │   返回 customer_message
# │   【告诉用户还缺什么信息】
# │
# ├── human_support
# │      ↓
# │   build_human_support_answer()
# │   【生成“需要人工处理”的回复】
# │      ↓
# │   status = pending_human
# │
# └── finish
#        ↓
#     build_investigation_answer()
#     【检查最终 Diagnosis 有没有真实 Evidence 支持】
#        ↓
#
#     Evidence 是否可靠？
#        │
#        ├── No
#        │   【Evidence 冲突 / 没有足够证据支持结论】
#        │      ↓
#        │   status = pending_human
#        │
#        └── Yes
#               ↓
#            生成最终 Diagnosis Answer
#            【保留有 Evidence 支持的事实和可能原因】
#               ↓
#            answer + outcome


# 让 Agent 判断
# ↓
# 得到 Diagnosis
# ↓
# 再用 Evidence 检查 Diagnosis
# ↓
# 输出可信结果
