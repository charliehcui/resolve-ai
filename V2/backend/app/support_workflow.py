import json
import time
from typing import Literal, TypedDict

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langsmith import traceable

from backend.app.config import get_settings
from backend.app.customer_agent import sum_token_usage
from backend.app.handoff import SupportHandoff, update_handoff_identifiers
from backend.app.models import UserContext
from backend.app.support_agent import (
    MAX_CONSECUTIVE_ERRORS,
    MAX_INVESTIGATION_MS,
    MAX_TOOL_CALLS,
    MAX_TOOL_ERRORS,
    SupportInvestigationResult,
    plan_support_step,
    render_control_result,
)
from backend.app.support_cases import get_case_id, update_case
from backend.app.support_evidence import EvidenceRecord, load_evidence
from backend.app.support_tools import TOOL_FUNCTIONS, create_ticket, execute_tool_batch
from backend.app.trace import current_trace_id


class SupportInvestigationState(TypedDict, total=False):
    question: str
    user: dict[str, str]
    conversation_id: str
    case_id: str
    handoff: dict[str, object]
    evidence: list[dict[str, object]]
    proposed_calls: list[dict[str, object]]
    answer: str
    status: str
    usage: dict[str, int | None]
    models_used: list[str]
    started_at: float


ERROR_EVIDENCE_STATUSES = {"forbidden", "unavailable", "error"}
CONTROL_TOOL_NAMES = {"FinishInvestigation", "RequestInformation", "EscalateInvestigation"}


def load_evidence_records(items: list[dict[str, object]]) -> list[EvidenceRecord]:
    records: list[EvidenceRecord] = []

    for item in items:
        records.append(EvidenceRecord(**item))

    return records


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


def support_plan_node(state: SupportInvestigationState) -> SupportInvestigationState:
    handoff = SupportHandoff.model_validate(state["handoff"])
    evidence = load_evidence_records(state.get("evidence", []))

    if handoff.missing_fields:
        labels = "、".join(handoff.missing_fields)
        return {"answer": f"继续调查前请补充：{labels}。", "status": "needs_info"}

    elapsed_ms = int((time.perf_counter() - state["started_at"]) * 1000)
    tool_budget_reached = len(evidence) >= MAX_TOOL_CALLS
    time_budget_reached = elapsed_ms >= MAX_INVESTIGATION_MS
    if tool_budget_reached or time_budget_reached:
        return {"answer": "调查已达到本次预算上限，当前证据已保留，等待人工继续处理。", "status": "pending_human"}

    if count_evidence_errors(evidence) >= MAX_TOOL_ERRORS:
        return {"answer": "Investigation reached the error budget and stopped safely with current evidence preserved.", "status": "pending_human"}

    remaining_tool_calls = MAX_TOOL_CALLS - len(evidence)
    calls, usage, model_name = plan_support_step(state["question"], handoff, evidence, remaining_tool_calls)
    total_usage = sum_token_usage(state.get("usage", {}), usage)
    models_used = list(state.get("models_used", []))
    models_used.append(model_name)

    read_calls: list[dict[str, object]] = []
    for call in calls:
        if call["name"] in TOOL_FUNCTIONS:
            read_calls.append(call)

    if read_calls:
        if len(read_calls) > remaining_tool_calls:
            return {"answer": "模型提出的检查超过工具预算，调查已安全停止并等待人工处理。", "status": "pending_human", "usage": total_usage, "models_used": models_used}

        previous_calls: set[tuple[str, str]] = set()
        for record in evidence:
            previous_calls.add((record.tool_name, json_key(record.request)))

        new_calls: list[dict[str, object]] = []
        for call in read_calls:
            call_name = str(call["name"])
            call_args = dict(call.get("args") or {})
            call_key = (call_name, json_key(call_args))
            if call_key not in previous_calls:
                new_calls.append(call)

        if not new_calls:
            return {"answer": "没有新的安全检查可执行，当前证据已保留，等待人工继续处理。", "status": "pending_human", "usage": total_usage, "models_used": models_used}

        return {"proposed_calls": new_calls, "usage": total_usage, "models_used": models_used}

    control_call = None
    for call in calls:
        if call["name"] in CONTROL_TOOL_NAMES:
            control_call = call
            break

    if control_call is None:
        return {"answer": "模型没有返回允许的下一步，调查已安全停止。", "status": "pending_human", "usage": total_usage, "models_used": models_used}

    answer, status = render_control_result(control_call, evidence)
    return {"answer": answer, "status": status, "usage": total_usage, "models_used": models_used}


def json_key(value: dict[str, object]) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def support_execute_node(state: SupportInvestigationState) -> SupportInvestigationState:
    user = UserContext.model_validate(state["user"])
    handoff = SupportHandoff.model_validate(state["handoff"])
    evidence = load_evidence_records(state.get("evidence", []))
    new_evidence = execute_tool_batch(state["case_id"], user, state["proposed_calls"], handoff.known_shop_id or "", handoff.known_order_id or "", handoff.known_sku or "")
    combined = [*evidence, *new_evidence]

    consecutive_errors = 0
    for record in reversed(combined):
        if record.status in ERROR_EVIDENCE_STATUSES:
            consecutive_errors += 1
        else:
            break

    result: SupportInvestigationState = {
        "evidence": dump_evidence_records(combined),
        "proposed_calls": [],
    }
    total_errors = count_evidence_errors(combined)
    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS or total_errors >= MAX_TOOL_ERRORS:
        result.update({"answer": "连续内部检查失败，调查已安全停止并等待人工处理。", "status": "pending_human"})
    return result


def route_support(state: SupportInvestigationState) -> Literal["execute", "plan", "done"]:
    if state.get("status"):
        return "done"
    if state.get("proposed_calls"):
        return "execute"
    return "plan"


def build_support_investigation_graph() -> StateGraph:
    graph = StateGraph(SupportInvestigationState)
    graph.add_node("plan", support_plan_node)
    graph.add_node("execute", support_execute_node)
    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", route_support, {"execute": "execute", "plan": "plan", "done": END})
    graph.add_conditional_edges("execute", route_support, {"execute": "execute", "plan": "plan", "done": END})
    return graph


@traceable(name="support_conversation_turn", run_type="chain")
def run_support_graph(question: str, user: UserContext, conversation_id: str) -> SupportInvestigationResult:
    settings = get_settings()
    started_at = time.perf_counter()
    handoff = update_handoff_identifiers(conversation_id, user, question)
    case_id = get_case_id(conversation_id, user)
    evidence = load_evidence(case_id)

    with PostgresSaver.from_conn_string(settings.postgres_url) as checkpointer:
        checkpointer.setup()
        graph_builder = build_support_investigation_graph()
        graph = graph_builder.compile(checkpointer=checkpointer)

        initial_state: SupportInvestigationState = {
            "question": question,
            "user": user.model_dump(),
            "conversation_id": conversation_id,
            "case_id": case_id,
            "handoff": handoff.model_dump(),
            "evidence": dump_evidence_records(evidence),
            "usage": {},
            "models_used": [],
            "started_at": started_at,
        }
        graph_config = {
            "configurable": {
                "thread_id": conversation_id,
                "checkpoint_ns": "support",
            },
            "recursion_limit": 20,
        }
        result = graph.invoke(initial_state, config=graph_config)

    final_evidence = load_evidence_records(result.get("evidence", []))
    total_latency_ms = int((time.perf_counter() - started_at) * 1000)
    status = result.get("status", "pending_human")
    answer = result.get("answer", "调查未形成可核验结果，等待人工继续处理。")
    update_case(case_id, status, answer, len(final_evidence), total_latency_ms)

    ticket_id = None
    if status == "pending_human":
        error_count = count_evidence_errors(final_evidence)
        budget_reached = len(final_evidence) >= MAX_TOOL_CALLS or error_count >= MAX_TOOL_ERRORS
        if budget_reached:
            trigger = "budget_reached"
        else:
            trigger = "support_unresolved"

        ticket = create_ticket(user, conversation_id, trigger, answer)
        ticket_id = str(ticket["ticket_id"])

    evidence_ids: list[str] = []
    tool_path: list[str] = []
    for record in final_evidence:
        evidence_ids.append(record.evidence_id)
        tool_path.append(record.tool_name)

    return SupportInvestigationResult(
        answer=answer,
        status=status,
        case_id=case_id,
        evidence_ids=evidence_ids,
        tool_path=tool_path,
        tool_call_count=len(final_evidence),
        models_used=result.get("models_used", []),
        usage=result.get("usage", {}),
        trace_id=current_trace_id(),
        ticket_id=ticket_id,
    )
