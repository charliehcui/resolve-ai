import json
import time
from typing import Literal, TypedDict

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langsmith import traceable

from backend.app.config import get_settings
from backend.app.customer_agent import sum_token_usage
from backend.app.handoff import SupportHandoffRecord, update_support_ids
from backend.app.models import UserContext
from backend.app.support_agent import (
    MAX_CONSECUTIVE_ERRORS,
    MAX_INVESTIGATION_MS,
    MAX_TOOL_CALLS,
    MAX_TOOL_ERRORS,
    HumanSupportRequired,
    MissingInformationRequest,
    SupportAgentResult,
    SupportNextStep,
    build_support_answer,
    decide_support_next_step,
)
from backend.app.support_cases import get_case_id, update_case
from backend.app.support_evidence import EvidenceRecord, load_evidence
from backend.app.support_tools import TOOL_FUNCTIONS, create_ticket, execute_tool_batch
from backend.app.trace import current_trace_id


class SupportWorkflowState(TypedDict, total=False):
    question: str
    user: dict[str, str]
    conversation_id: str
    case_id: str
    handoff: dict[str, object]
    evidence: list[dict[str, object]]
    tool_calls: list[dict[str, object]]
    support_decision: dict[str, object]
    answer: str
    status: str
    usage: dict[str, int | None]
    started_at: float


ERROR_EVIDENCE_STATUSES = {"forbidden", "unavailable", "error"}


def load_evidence_records(items: list[dict[str, object]]) -> list[EvidenceRecord]:
    records: list[EvidenceRecord] = []

    for item in items:
        records.append(EvidenceRecord.model_validate(item))

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


def support_agent_node(state: SupportWorkflowState) -> SupportWorkflowState:
    handoff = SupportHandoffRecord.model_validate(state["handoff"])
    evidence = load_evidence_records(state.get("evidence", []))

    if len(handoff.missing_fields) > 0:
        labels = "、".join(handoff.missing_fields)
        missing_information = MissingInformationRequest(missing_fields=handoff.missing_fields, customer_message=f"继续调查前请补充：{labels}。")
        support_decision = SupportNextStep(next_step="request_information", missing_information=missing_information)
        return {"support_decision": support_decision.model_dump(), "tool_calls": []}

    elapsed_ms = int((time.perf_counter() - state["started_at"]) * 1000)
    tool_budget_reached = len(evidence) >= MAX_TOOL_CALLS
    time_budget_reached = elapsed_ms >= MAX_INVESTIGATION_MS

    if tool_budget_reached or time_budget_reached:
        human_support = HumanSupportRequired(reason="调查已达到本次预算上限，当前证据已保留。")
        support_decision = SupportNextStep(next_step="human_support", human_support=human_support)
        return {"support_decision": support_decision.model_dump(), "tool_calls": []}

    consecutive_errors = 0

    for record in reversed(evidence):
        if record.status in ERROR_EVIDENCE_STATUSES:
            consecutive_errors += 1
        else:
            break

    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS or count_evidence_errors(evidence) >= MAX_TOOL_ERRORS:
        human_support = HumanSupportRequired(reason="调查已达到错误预算，当前证据已保留。")
        support_decision = SupportNextStep(next_step="human_support", human_support=human_support)
        return {"support_decision": support_decision.model_dump(), "tool_calls": []}

    remaining_tool_calls = MAX_TOOL_CALLS - len(evidence)
    support_decision, usage = decide_support_next_step(state["question"], handoff, evidence, remaining_tool_calls)
    total_usage = sum_token_usage(state.get("usage", {}), usage)

    if support_decision.next_step == "use_tool":
        read_calls: list[dict[str, object]] = []

        for tool_call in support_decision.tool_calls:
            if tool_call["name"] in TOOL_FUNCTIONS:
                read_calls.append(tool_call)

        if len(read_calls) != len(support_decision.tool_calls):
            human_support = HumanSupportRequired(reason="模型返回了未注册的查询工具。")
            support_decision = SupportNextStep(next_step="human_support", human_support=human_support)
            return {"support_decision": support_decision.model_dump(), "tool_calls": [], "usage": total_usage}

        if len(read_calls) > remaining_tool_calls:
            human_support = HumanSupportRequired(reason="模型提出的检查超过工具预算。")
            support_decision = SupportNextStep(next_step="human_support", human_support=human_support)
            return {"support_decision": support_decision.model_dump(), "tool_calls": [], "usage": total_usage}

        previous_calls: set[tuple[str, str]] = set()

        for record in evidence:
            previous_calls.add((record.tool_name, json_key(record.request)))

        new_calls: list[dict[str, object]] = []

        for tool_call in read_calls:
            tool_name = str(tool_call["name"])
            tool_args = dict(tool_call.get("args") or {})
            tool_key = (tool_name, json_key(tool_args))

            if tool_key in previous_calls:
                continue

            new_calls.append(tool_call)

        if len(new_calls) == 0:
            human_support = HumanSupportRequired(reason="没有新的安全查询可以执行。")
            support_decision = SupportNextStep(next_step="human_support", human_support=human_support)
            return {"support_decision": support_decision.model_dump(), "tool_calls": [], "usage": total_usage}

        support_decision.tool_calls = new_calls
        return {"support_decision": support_decision.model_dump(), "tool_calls": new_calls, "usage": total_usage}

    return {"support_decision": support_decision.model_dump(), "tool_calls": [], "usage": total_usage}


def json_key(value: dict[str, object]) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def execute_support_tools_node(state: SupportWorkflowState) -> SupportWorkflowState:
    user = UserContext.model_validate(state["user"])
    handoff = SupportHandoffRecord.model_validate(state["handoff"])
    evidence = load_evidence_records(state.get("evidence", []))
    new_evidence = execute_tool_batch(state["case_id"], user, state["tool_calls"], handoff.known_shop_id or "", handoff.known_order_id or "", handoff.known_sku or "")
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
        support_decision = SupportNextStep(next_step="human_support", human_support=human_support)
        result["support_decision"] = support_decision.model_dump()

    return result


def route_support_next_step(state: SupportWorkflowState) -> Literal["use_tool", "request_information", "finish", "human_support"]:
    support_decision = SupportNextStep.model_validate(state["support_decision"])
    return support_decision.next_step


def request_information_node(state: SupportWorkflowState) -> SupportWorkflowState:
    support_decision = SupportNextStep.model_validate(state["support_decision"])
    evidence = load_evidence_records(state.get("evidence", []))
    answer, status = build_support_answer(support_decision, evidence)
    return {"answer": answer, "status": status}


def finish_investigation_node(state: SupportWorkflowState) -> SupportWorkflowState:
    support_decision = SupportNextStep.model_validate(state["support_decision"])
    evidence = load_evidence_records(state.get("evidence", []))
    answer, status = build_support_answer(support_decision, evidence)
    return {"answer": answer, "status": status}


def human_support_node(state: SupportWorkflowState) -> SupportWorkflowState:
    support_decision = SupportNextStep.model_validate(state["support_decision"])
    evidence = load_evidence_records(state.get("evidence", []))
    answer, status = build_support_answer(support_decision, evidence)
    return {"answer": answer, "status": status}


def build_support_workflow() -> StateGraph:
    workflow = StateGraph(SupportWorkflowState)
    workflow.add_node("support_agent", support_agent_node)
    workflow.add_node("execute_tools", execute_support_tools_node)
    workflow.add_node("request_information", request_information_node)
    workflow.add_node("finish", finish_investigation_node)
    workflow.add_node("human_support", human_support_node)
    workflow.add_edge(START, "support_agent")
    workflow.add_conditional_edges(
        "support_agent",
        route_support_next_step,
        {
            "use_tool": "execute_tools",
            "request_information": "request_information",
            "finish": "finish",
            "human_support": "human_support",
        },
    )
    workflow.add_edge("execute_tools", "support_agent")
    workflow.add_edge("request_information", END)
    workflow.add_edge("finish", END)
    workflow.add_edge("human_support", END)
    return workflow


@traceable(name="support_conversation_turn", run_type="chain")
def run_support_workflow(question: str, user: UserContext, conversation_id: str) -> SupportAgentResult:
    settings = get_settings()
    started_at = time.perf_counter()
    handoff = update_support_ids(conversation_id, user, question)
    case_id = get_case_id(conversation_id, user)
    evidence = load_evidence(case_id)

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
    )
