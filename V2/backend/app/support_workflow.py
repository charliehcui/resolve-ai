import json
import re
import time
from typing import Literal, TypedDict

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langsmith import traceable

from backend.app.config import get_settings
from backend.app.customer_agent import sum_token_usage
from backend.app.handoff import SupportHandoffRecord, update_support_ids
from backend.app.models import UserContext
from backend.app.support_action_plans import create_action_plan
from backend.app.support_cases import get_case_id, update_case
from backend.app.support_diagnosis import (
    MAX_CONSECUTIVE_ERRORS,
    MAX_INVESTIGATION_MS,
    MAX_TOOL_CALLS,
    MAX_TOOL_ERRORS,
    CandidateAction,
    ClaimWithEvidence,
    HumanSupportRequired,
    InvestigationComplete,
    MissingInformationRequest,
    SupportAgentResult,
    SupportNextStep,
    build_support_answer,
    decide_support_next_step,
    has_shipment_evidence_conflict,
    primary_read_tools,
)
from backend.app.support_evidence import EvidenceRecord, load_evidence
from backend.app.support_tools import READ_TOOL_FUNCTIONS, create_engineer_ticket, execute_read_tool_batch
from backend.app.trace import current_trace_id
from backend.app.user_intent import action_request


class SupportWorkflowState(TypedDict, total=False):
    question: str
    user: dict[str, str]
    conversation_id: str
    case_id: str
    handoff: dict[str, object]
    evidence: list[dict[str, object]]
    tool_calls: list[dict[str, object]]
    support_next_step: dict[str, object]
    answer: str
    status: str
    usage: dict[str, int | None]
    started_at: float
    action_plan: dict[str, object] | None


ERROR_EVIDENCE_STATUSES = {"forbidden", "unavailable", "error"}

# 普通 dict ↓ EvidenceRecord
def load_evidence_records(items: list[dict[str, object]]) -> list[EvidenceRecord]:
    records: list[EvidenceRecord] = []

    for item in items:
        records.append(EvidenceRecord.model_validate(item))

    return records

#EvidenceRecord ↓ 普通 dict   方便保存到 Workflow State
def dump_evidence_records(records: list[EvidenceRecord]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []

    for record in records:
        items.append(record.model_dump())

    return items


def count_evidence_errors(records: list[EvidenceRecord]) -> int:
    error_count = 0

    for record in records:
        if record.status in ERROR_EVIDENCE_STATUSES:
            error_count += 1

    return error_count


def missing_order_recovery_candidate(records: list[EvidenceRecord], handoff: SupportHandoffRecord) -> CandidateAction | None:
    if not handoff.known_order_id or handoff.known_sku or action_request(handoff.customer_problem, "recovery") is False or any(word in handoff.customer_problem.casefold() for word in ("发货", "出库", "库存", "只读", "仅调查", "shipment", "shipping", "inventory", "diagnose only")):
        return None
    indexed = {}
    for record in records:
        if record.source_service == "support" or record.request.get("shop_id") != handoff.known_shop_id:
            continue
        if record.tool_name in {"GetOrder", "GetOrderProcessRecords"} and record.request.get("order_id") == handoff.known_order_id or record.tool_name in {"GetShopSyncStatus", "GetShopConnectionStatus"}:
            indexed[record.tool_name] = record
    if not {"GetOrder", "GetOrderProcessRecords", "GetShopSyncStatus", "GetShopConnectionStatus"}.issubset(indexed):
        return None
    source, processing, sync, connection = [indexed[name] for name in ("GetOrder", "GetOrderProcessRecords", "GetShopSyncStatus", "GetShopConnectionStatus")]
    if source.status != "success" or source.response.get("payment_status") != "paid" or any(source.response.get(field) is None for field in ("event_id", "sku", "quantity", "amount_minor")):
        return None
    if processing.status != "empty" or processing.response.get("empty") is not True or any(processing.response.get(field) is not None for field in ("receipt", "task", "merchant_order")):
        return None
    if sync.status != "success" or sync.response.get("sync_enabled") is not True or connection.status != "success" or connection.response.get("connection_status") != "authorized":
        return None
    return CandidateAction(action_type="retry_order_sync", reason="平台已付款订单存在，管理软件接收记录缺失，当前连接与同步已确认；生成待确认恢复方案，实际资格由计划层重新校验。", evidence_ids=[record.evidence_id for record in indexed.values()])


def has_confirmed_business_blocker(records: list[EvidenceRecord], handoff: SupportHandoffRecord) -> bool:
    scoped = [record for record in records if record.source_service != "support" and record.request.get("shop_id") == handoff.known_shop_id and ("order_id" not in record.request or record.request["order_id"] == handoff.known_order_id) and ("sku" not in record.request or record.request["sku"] == handoff.known_sku)]
    queried = {record.tool_name for record in scoped}
    primary_complete = primary_read_tools(handoff, handoff.customer_problem).issubset(queried)
    if primary_complete and has_shipment_evidence_conflict(scoped):
        return True
    indexed = {record.tool_name: record for record in scoped}
    order = indexed.get("GetOrder")
    if primary_complete and order is not None and (order.status == "not_found" or order.status == "success" and order.response.get("payment_status") in {"unpaid", "cancelled"}):
        return True
    task = indexed.get("GetWorkerTask")
    if primary_complete and action_request(handoff.customer_problem, "recovery") is False and task is not None and task.status == "success" and task.response.get("error_code") == "WORKER_INTERRUPTED" and task.response.get("retryable") is True:
        return True
    warehouse, platform, processing = [indexed.get(name) for name in ("GetWarehouseShipment", "GetPlatformShipment", "GetShipmentProcessRecords")]
    if primary_complete and warehouse is not None and processing is not None and platform is not None and warehouse.status == "success" and processing.status == "success" and platform.status == "not_found" and warehouse.response.get("warehouse_order_status") == "shipped" and warehouse.response.get("shipment_count") == 1:
        same_shipment = all(warehouse.response.get(field) and warehouse.response[field] == processing.response.get(field) for field in ("shipment_id", "carrier", "tracking_number")) and warehouse.response.get("shipment_version") == processing.response.get("version")
        sync, connection = indexed.get("GetShopSyncStatus"), indexed.get("GetShopConnectionStatus")
        if same_shipment and sync is not None and sync.status == "success":
            if sync.response.get("shipment_sync_enabled") is False:
                return True
            if connection is not None and connection.status == "success" and connection.response.get("connection_status") == "authorized" and sync.response.get("shipment_sync_enabled") is True and processing.response.get("task_status") == "failed" and processing.response.get("error_code") == "TRANSIENT_PROCESSING_ERROR":
                return True
    if primary_complete and all(record is not None and record.status == "success" for record in (warehouse, platform, processing)) and warehouse.response.get("shipment_count") == 1 and platform.response.get("status") == "shipped" and all(warehouse.response.get(field) and warehouse.response[field] == platform.response.get(field) for field in ("shipment_id", "tracking_number", "carrier")):
        return True
    stock = indexed.get("GetStockStatus")
    if primary_complete and stock is not None and stock.status in {"success", "empty"}:
        if stock.response.get("assessment") in {"consistent", "waiting", "insufficient_information"}:
            return True
        source = stock.response.get("warehouse") or {}
        target = stock.response.get("platform") or {}
        if stock.response.get("assessment") == "difference" and isinstance(source.get("version"), int) and isinstance(target.get("source_version"), int) and target["source_version"] > source["version"]:
            return True
    if missing_order_recovery_candidate(records, handoff) is not None:
        return True
    if not handoff.known_order_id or handoff.known_sku:
        return False

    latest: dict[str, EvidenceRecord] = {}
    for record in records:
        if record.source_service == "support" or record.request.get("shop_id") != handoff.known_shop_id:
            continue
        if record.tool_name in {"GetShopConnectionStatus", "GetShopSyncStatus"} or (record.tool_name in {"GetOrder", "GetOrderProcessRecords", "GetWorkerTask"} and record.request.get("order_id") == handoff.known_order_id):
            latest[record.tool_name] = record

    order = latest.get("GetOrder")
    processing = latest.get("GetOrderProcessRecords")
    if order is None or processing is None or order.status != "success" or processing.status != "success":
        return False

    facts = processing.response
    other_investigation = any(word in handoff.customer_problem.casefold() for word in ("发货", "出库", "库存", "授权", "连接", "限流", "shipment", "shipping", "delivery", "stock", "inventory", "authorization", "connection", "outage", "rate limit"))
    # 完整匹配或明确字段冲突都已足以结束导入调查；冲突交人工，不扩展到发货。
    if (not other_investigation or primary_complete) and facts.get("task_status") == "completed" and facts.get("merchant_order_count") == 1 and facts.get("merchant_order_id") and facts.get("merchant_sku") and order.response.get("payment_status") == "paid" and facts.get("event_id") and order.response.get("event_id") and facts.get("platform_sku") and order.response.get("sku") and all(order.response.get(field) is not None and facts.get(field) is not None for field in ("quantity", "amount_minor")):
        return True
    if facts.get("error_code") == "SKU_MAPPING_MISSING" and facts.get("task_status") == "blocked" and "merchant_sku" in facts and facts["merchant_sku"] is None:
        return True

    connection = latest.get("GetShopConnectionStatus")
    if connection is None or connection.status != "success":
        return False
    if connection.response.get("connection_status") == "auth_expired" and facts.get("task_status") == "blocked" and facts.get("error_code") == "CHANNEL_AUTH_EXPIRED":
        return True
    sync = latest.get("GetShopSyncStatus")
    if facts.get("task_status") == "blocked" and facts.get("error_code") in {"CHANNEL_AUTH_EXPIRED", "ORDER_SYNC_DISABLED"} and facts.get("merchant_order_count") == 0 and facts.get("merchant_order_id") is None and order.response.get("payment_status") == "paid" and connection.response.get("connection_status") == "authorized" and sync is not None and sync.status == "success" and sync.response.get("sync_enabled") is True:
        return True
    if facts.get("error_code") in {"WORKER_INTERRUPTED", "TRANSIENT_PROCESSING_ERROR"}:
        if any(word in handoff.customer_problem.casefold() for word in ("发货", "出库", "shipment", "shipping", "delivery")):
            return False
        task = latest.get("GetWorkerTask")
        sync = latest.get("GetShopSyncStatus")
        if task is None or sync is None or task.status != "success" or sync.status != "success":
            return False
        task_facts = task.response
        if connection.response.get("connection_status") != "authorized" or sync.response.get("sync_enabled") is not True or order.response.get("payment_status") != "paid":
            return False
        if not facts.get("task_id") or not facts.get("event_id") or facts["event_id"] != order.response.get("event_id"):
            return False
        if facts["error_code"] == "TRANSIENT_PROCESSING_ERROR" and facts.get("task_status") != "failed":
            return False
        return facts.get("task_status") in {"processing", "failed"} and facts.get("merchant_order_count") == 0 and facts.get("merchant_order_id") is None and task_facts.get("task_id") == facts["task_id"] and task_facts.get("event_id") == facts["event_id"] and task_facts.get("status") == facts["task_status"] and task_facts.get("error_code") == facts["error_code"] and task_facts.get("retryable") is True
    if facts.get("task_status") != "failed":
        return False
    channel_errors = {"unavailable": "CHANNEL_UNAVAILABLE", "rate_limited": "CHANNEL_RATE_LIMITED"}
    expected_error = channel_errors.get(connection.response.get("connection_status"))
    return expected_error is not None and facts.get("error_code") == expected_error


#在真正让agent判断之前，先用普通代码检查“现在还能不能继续调查
def decide_support_next_step_node(state: SupportWorkflowState) -> SupportWorkflowState:
    handoff = SupportHandoffRecord.model_validate(state["handoff"])
    evidence = load_evidence_records(state.get("evidence", []))

    if len(handoff.missing_fields) > 0:
        labels = "、".join(handoff.missing_fields)
        missing_information = MissingInformationRequest(missing_fields=handoff.missing_fields, customer_message=f"继续调查前请补充：{labels}。")
        support_next_step = SupportNextStep(next_step="request_information", missing_information=missing_information)

        return {
            "support_next_step": support_next_step.model_dump(),
            "tool_calls": [],
        }

    primary = primary_read_tools(handoff, state["question"])
    scoped = {record.tool_name: record for record in evidence if record.source_service != "support" and record.request.get("shop_id") == handoff.known_shop_id and record.request.get("order_id") == handoff.known_order_id}
    source, processing = scoped.get("GetOrder"), scoped.get("GetOrderProcessRecords")
    if primary == {"GetOrder", "GetOrderProcessRecords"} and source is not None and processing is not None:
        if source.status == "not_found":
            missing = MissingInformationRequest(missing_fields=["order_id"], customer_message=f"在店铺 {handoff.known_shop_id} 下未查到订单 {handoff.known_order_id} 的平台来源。请核对订单编号，并确认它属于这个店铺。未查到来源不能说明发生了同步故障。")
            return {"support_next_step": SupportNextStep(next_step="request_information", missing_information=missing).model_dump(), "tool_calls": []}
        payment = source.response.get("payment_status")
        if source.status == "success" and payment in {"unpaid", "cancelled"} and processing.status == "success" and processing.response.get("merchant_order_count") == 0 and processing.response.get("error_code") == "ORDER_NOT_PAID":
            label = "未付款" if payment == "unpaid" else "已取消"
            facts = [ClaimWithEvidence(text=f"平台订单的当前付款状态为 {payment}（{label}）。", evidence_ids=[source.evidence_id]), ClaimWithEvidence(text="商家处理记录显示 ORDER_NOT_PAID，未生成商家订单。", evidence_ids=[processing.evidence_id])]
            decision = InvestigationComplete(summary=f"订单{label}，不符合导入条件，未创建恢复方案。状态改变后需要重新核对，不会自动补回该订单。", confirmed_facts=facts, outcome="diagnosed")
            return {"support_next_step": SupportNextStep(next_step="finish", investigation_complete=decision).model_dump(), "tool_calls": []}

    elapsed_ms = int((time.perf_counter() - state["started_at"]) * 1000)
    tool_budget_reached = len(evidence) >= MAX_TOOL_CALLS
    time_budget_reached = elapsed_ms >= MAX_INVESTIGATION_MS

    terminal_only = tool_budget_reached or time_budget_reached or has_confirmed_business_blocker(evidence, handoff)
    if terminal_only and not evidence:
        human_support = HumanSupportRequired(reason="调查已达到本次预算上限，当前证据已保留。")
        support_next_step = SupportNextStep(next_step="human_support", human_support=human_support)

        return {
            "support_next_step": support_next_step.model_dump(),
            "tool_calls": [],
        }

    consecutive_errors = 0

    for record in reversed(evidence):
        if record.status in ERROR_EVIDENCE_STATUSES:
            consecutive_errors += 1
        else:
            break

    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS or count_evidence_errors(evidence) >= MAX_TOOL_ERRORS:
        human_support = HumanSupportRequired(reason="调查已达到错误预算，当前证据已保留。")
        support_next_step = SupportNextStep(next_step="human_support", human_support=human_support)

        return {
            "support_next_step": support_next_step.model_dump(),
            "tool_calls": [],
        }

    # 预算耗尽或业务阻碍已确认后，只允许一次终止诊断，不再扩展读取。
    remaining_tool_calls = 0 if terminal_only else MAX_TOOL_CALLS - len(evidence)
    support_next_step, usage = decide_support_next_step(state["question"], handoff, evidence, remaining_tool_calls)
    total_usage = sum_token_usage(state.get("usage", {}), usage)

    if support_next_step.next_step == "use_tool":
        read_calls: list[dict[str, object]] = []

        for tool_call in support_next_step.tool_calls:
            if tool_call["name"] in READ_TOOL_FUNCTIONS:
                read_calls.append(tool_call)

        if len(read_calls) != len(support_next_step.tool_calls):
            human_support = HumanSupportRequired(reason="模型返回了未注册的查询工具。")
            support_next_step = SupportNextStep(next_step="human_support", human_support=human_support)

            return {
                "support_next_step": support_next_step.model_dump(),
                "tool_calls": [],
                "usage": total_usage,
            }

        if len(read_calls) > remaining_tool_calls:
            human_support = HumanSupportRequired(reason="模型提出的检查超过工具预算。")
            support_next_step = SupportNextStep(next_step="human_support", human_support=human_support)

            return {
                "support_next_step": support_next_step.model_dump(),
                "tool_calls": [],
                "usage": total_usage,
            }

        previous_calls: set[tuple[str, str]] = set()

        for record in evidence:
            previous_calls.add((record.tool_name, build_tool_call_key(record.request)))

        new_calls: list[dict[str, object]] = []

        for tool_call in read_calls:
            tool_name = str(tool_call["name"])
            tool_args = dict(tool_call.get("args") or {})
            tool_key = (tool_name, build_tool_call_key(tool_args))

            if tool_key in previous_calls:
                continue

            new_calls.append(tool_call)
            previous_calls.add(tool_key)

        if len(new_calls) == 0:
            if evidence:
                # 模型只重复已有读取时，只允许一次无读取工具的终止判断。
                support_next_step, usage = decide_support_next_step(state["question"], handoff, evidence, 0)
                total_usage = sum_token_usage(total_usage, usage)
                if support_next_step.next_step != "use_tool":
                    return {"support_next_step": support_next_step.model_dump(), "tool_calls": [], "usage": total_usage}
            human_support = HumanSupportRequired(reason="没有新的安全查询可以执行。")
            support_next_step = SupportNextStep(next_step="human_support", human_support=human_support)

            return {
                "support_next_step": support_next_step.model_dump(),
                "tool_calls": [],
                "usage": total_usage,
            }

        support_next_step.tool_calls = new_calls

        return {
            "support_next_step": support_next_step.model_dump(),
            "tool_calls": new_calls,
            "usage": total_usage,
        }

    return {
        "support_next_step": support_next_step.model_dump(),
        "tool_calls": [],
        "usage": total_usage,
    }


def build_tool_call_key(value: dict[str, object]) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def execute_read_tools_node(state: SupportWorkflowState) -> SupportWorkflowState:
    user = UserContext.model_validate(state["user"])
    handoff = SupportHandoffRecord.model_validate(state["handoff"])
    evidence = load_evidence_records(state.get("evidence", []))

    new_evidence = execute_read_tool_batch(
        state["case_id"],
        user,
        state["tool_calls"],
        handoff.known_shop_id or "",
        handoff.known_order_id or "",
        handoff.known_sku or "",
    )

    combined_evidence = [*evidence, *new_evidence]

    consecutive_errors = 0

    for record in reversed(combined_evidence):
        if record.status in ERROR_EVIDENCE_STATUSES:
            consecutive_errors += 1
        else:
            break

    result: SupportWorkflowState = {
        "evidence": dump_evidence_records(combined_evidence),
        "tool_calls": [],
    }

    total_errors = count_evidence_errors(combined_evidence)

    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS or total_errors >= MAX_TOOL_ERRORS:
        human_support = HumanSupportRequired(reason="连续内部查询失败，当前证据已保留。")
        support_next_step = SupportNextStep(next_step="human_support", human_support=human_support)
        result["support_next_step"] = support_next_step.model_dump()

    return result


def choose_support_next_step(state: SupportWorkflowState) -> Literal["use_tool", "request_information", "finish", "human_support"]:
    support_next_step = SupportNextStep.model_validate(state["support_next_step"])

    return support_next_step.next_step


def build_missing_information_answer_node(state: SupportWorkflowState) -> SupportWorkflowState:
    support_next_step = SupportNextStep.model_validate(state["support_next_step"])
    evidence = load_evidence_records(state.get("evidence", []))
    answer, status = build_support_answer(support_next_step, evidence)

    return {
        "answer": answer,
        "status": status,
    }


def build_investigation_answer_node(state: SupportWorkflowState) -> SupportWorkflowState:
    support_next_step = SupportNextStep.model_validate(state["support_next_step"])
    evidence = load_evidence_records(state.get("evidence", []))
    diagnosis = support_next_step.investigation_complete
    question = state.get("question", state["handoff"]["customer_problem"])
    recovery_declined = action_request(question, "recovery") is False
    if diagnosis is not None and recovery_declined and diagnosis.recommended_action is not None:
        diagnosis.recommended_action = None
        diagnosis.summary = "本次只提供已确认的调查事实，未创建恢复方案。"
    if diagnosis is not None and diagnosis.recommended_action is None:
        candidate = None if recovery_declined else missing_order_recovery_candidate(evidence, SupportHandoffRecord.model_validate(state["handoff"]))
        if candidate is not None:
            diagnosis.recommended_action = candidate
            diagnosis.summary = "平台已付款订单存在，管理软件接收记录缺失，当前连接正常且同步已开启。将创建待确认的单笔恢复方案。"
        elif any(re.search(r"(?:需要|建议|请求|交给|转交|转至|转).{0,8}(?:人工|工程师)|\b(?:requires? human|needs? human|escalate to human)\b", clause, flags=re.IGNORECASE) and not re.search(r"无需|不需要|不要|不必|\b(?:not|no need|do not)\b", clause, flags=re.IGNORECASE) for clause in re.split(r"[。；;.!?\n]", diagnosis.summary)):
            support_next_step = SupportNextStep(next_step="human_support", human_support=HumanSupportRequired(reason=diagnosis.summary, known_facts=diagnosis.confirmed_facts, unknowns=diagnosis.unknowns))
    answer, status = build_support_answer(support_next_step, evidence)
    if status == "pending_human" or diagnosis is None or diagnosis.recommended_action is None:
        return {"answer": answer, "status": status, "action_plan": None}
    user = UserContext.model_validate(state["user"])
    try:
        plan = create_action_plan(user, state["case_id"], diagnosis.recommended_action)
    except ValueError as error:
        return {"answer": f"{answer}\n建议动作未通过当前事实检查：{error}。需要人工进一步确认。", "status": "pending_human", "action_plan": None}
    if plan["status"] == "user_action_required":
        answer += f"\n{plan['instructions']}"
        status = "user_action_required"
    elif plan["status"] == "no_action_needed":
        answer += "\n当前后台事实已满足目标，无需执行修复。"
        status = "diagnosed"
    elif plan["status"] == "proposed":
        if plan["approval_requirement"] == "user_confirmation":
            answer += f"\n建议执行 {plan['action_type']}。是否确认执行这项修复？"
        else:
            answer += f"\n建议执行 {plan['action_type']}，需要公司管理员审批。"
        status = "awaiting_confirmation"
    else:
        answer += f"\n已有计划的当前状态：{plan['status']}。"
    return {"answer": answer, "status": status, "action_plan": plan, "evidence": dump_evidence_records(load_evidence(state["case_id"]))}


def build_human_support_answer_node(state: SupportWorkflowState) -> SupportWorkflowState:
    support_next_step = SupportNextStep.model_validate(state["support_next_step"])
    evidence = load_evidence_records(state.get("evidence", []))
    answer, status = build_support_answer(support_next_step, evidence)

    return {
        "answer": answer,
        "status": status,
    }


def build_support_workflow() -> StateGraph:
    workflow = StateGraph(SupportWorkflowState)

    workflow.add_node("decide_support_next_step", decide_support_next_step_node)
    workflow.add_node("execute_read_tools", execute_read_tools_node)
    workflow.add_node("build_missing_information_answer", build_missing_information_answer_node)
    workflow.add_node("build_investigation_answer", build_investigation_answer_node)
    workflow.add_node("build_human_support_answer", build_human_support_answer_node)

    workflow.add_edge(START, "decide_support_next_step")

    workflow.add_conditional_edges(
        "decide_support_next_step",
        choose_support_next_step,
        {
            "use_tool": "execute_read_tools",
            "request_information": "build_missing_information_answer",
            "finish": "build_investigation_answer",
            "human_support": "build_human_support_answer",
        },
    )

    workflow.add_edge("execute_read_tools", "decide_support_next_step")
    workflow.add_edge("build_missing_information_answer", END)
    workflow.add_edge("build_investigation_answer", END)
    workflow.add_edge("build_human_support_answer", END)

    return workflow


@traceable(name="support_conversation_turn", run_type="chain")
def run_support_workflow(question: str, user: UserContext, conversation_id: str) -> SupportAgentResult:
    settings = get_settings()
    started_at = time.perf_counter()

    handoff = update_support_ids(conversation_id, user, question)
    case_id = get_case_id(conversation_id, user)
    # Historical Evidence remains persisted. Each new investigation reads current facts.
    evidence = []

    with PostgresSaver.from_conn_string(settings.postgres_url) as checkpointer:
        checkpointer.setup()

        workflow_builder = build_support_workflow()
        workflow = workflow_builder.compile(checkpointer=checkpointer)

        initial_state: SupportWorkflowState = {
            "question": question,
            "user": user.model_dump(),
            "conversation_id": conversation_id,
            "case_id": case_id,
            "handoff": handoff.model_dump(),
            "evidence": dump_evidence_records(evidence),
            "usage": {},
            "started_at": started_at,
            "action_plan": None,
        }

        workflow_config = {
            "configurable": {
                "thread_id": conversation_id,
                "checkpoint_ns": "support",
            },
            "recursion_limit": 20,
        }

        result = workflow.invoke(initial_state, config=workflow_config)

    final_evidence = load_evidence_records(result.get("evidence", []))
    total_latency_ms = int((time.perf_counter() - started_at) * 1000)

    status = result.get("status", "pending_human")
    answer = result.get("answer", "调查未形成可核验结果，等待人工继续处理。")

    if status == "pending_human" and action_request(question, "human") is False:
        facts = answer.split("已确认事实：", 1)
        answer = "现有证据尚不足以完成自动调查。已按您的要求停止处理，未创建人工工单。"
        if len(facts) == 2:
            answer += "\n已确认事实：" + facts[1]
        status = "user_action_required"

    update_case(case_id, status, answer, len(final_evidence), total_latency_ms)

    ticket_id = None

    if status == "pending_human":
        error_count = count_evidence_errors(final_evidence)
        budget_reached = len(final_evidence) >= MAX_TOOL_CALLS or error_count >= MAX_TOOL_ERRORS

        if budget_reached:
            trigger = "budget_reached"
        else:
            trigger = "support_unresolved"

        ticket = create_engineer_ticket(user, conversation_id, trigger, answer)
        ticket_id = str(ticket["ticket_id"])

    evidence_ids: list[str] = []
    tool_path: list[str] = []

    for record in final_evidence:
        evidence_ids.append(record.evidence_id)
        tool_path.append(record.tool_name)

    return SupportAgentResult(
        answer=answer,
        status=status,
        case_id=case_id,
        evidence_ids=evidence_ids,
        tool_path=tool_path,
        tool_call_count=len(final_evidence),
        usage=result.get("usage", {}),
        trace_id=current_trace_id(),
        ticket_id=ticket_id,
        action_plan=result.get("action_plan"),
        action_plan_id=(result.get("action_plan") or {}).get("action_id"),
    )


# run_support_workflow()
# 【一轮 Support Agent 的总入口】
# ↓
# 创建 SupportWorkflowState
# 【保存 question / user / handoff / case / evidence 等流程数据】
# ↓
# START
# ↓
# decide_support_next_step_node()
# 【检查是否还能继续调查，然后让 Agent 决定下一步】
# ↓
# decide_support_next_step()
# 【LLM 根据 Handoff + 用户消息 + Evidence 做决定】
# ↓
# SupportNextStep
# 【决定走哪条 LangGraph 分支】
# ↓
#
# ├── use_tool
# │      ↓
# │   execute_read_tools_node()
# │   【执行 Agent 选择的 Read Tools】
# │      ↓
# │   Evidence
# │   【把后台查询结果保存成调查证据】
# │      ↓
# │   回到 decide_support_next_step_node()
# │   【带着新 Evidence 继续判断】
# │
# │
# ├── request_information
# │      ↓
# │   MissingInformationRequest
# │   【记录缺少 shop_id / order_id / sku 等信息】
# │      ↓
# │   build_missing_information_answer_node()
# │   【生成向用户补问信息的回复】
# │      ↓
# │   END
# │   【等待用户下一条消息】
# │
# │
# ├── finish
# │      ↓
# │   InvestigationComplete
# │   【完整 Diagnosis：事实、原因、未知项、建议 Action】
# │      ↓
# │   build_investigation_answer_node()
# │   【生成诊断结果，并处理 recommended_action】
# │      ↓
# │   recommended_action？
# │      │
# │      ├── No
# │      │      ↓
# │      │   返回 Diagnosis
# │      │      ↓
# │      │     END
# │      │
# │      └── Yes
# │             ↓
# │         CandidateAction
# │         【LLM 建议执行什么，以及依据哪些 Evidence】
# │             ↓
# │         create_action_plan()
# │         【Python 验证建议，并创建正式 Action Plan】
# │             ↓
# │         Action Plan
# │             │
# │             ├── user_action_required
# │             │   【需要用户自己操作】
# │             │
# │             ├── no_action_needed
# │             │   【当前已经正常，不需要修复】
# │             │
# │             └── proposed
# │                 【可以执行，等待 User / Admin Approval】
# │             ↓
# │            END
# │
# │
# └── human_support
#        ↓
#    HumanSupportRequired
#    【记录为什么自动调查无法继续】
#        ↓
#    build_human_support_answer_node()
#    【生成转人工回复】
#        ↓
#    status = pending_human
#        ↓
#       END
#
#
# LangGraph 结束
# ↓
# update_case()
# 【保存这一轮调查状态和结果】
# ↓
# pending_human？    LangGraph 整个流程结束后， 在外部判断是否需要人工介入， LangGraph 内部只负责把状态标记为 pending_human
# │
# ├── Yes → create_engineer_ticket()
# │          【创建人工工程师 Ticket】
# │
# └── No
#
# ↓
# SupportAgentResult
# 【整轮 Support Workflow 最终返回结果】
