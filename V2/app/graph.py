from typing import Literal, TypedDict

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langsmith import traceable

from app.config import get_settings
from app.customer import add_usage, answer_customer_question, no_search_answer, plan_customer_query
from app.docs import retrieve_documents
from app.evidence import EvidenceRecord, load_evidence
from app.models import AuthContext, CustomerAnswer, CustomerQueryPlan, RetrievedChunk
from app.support import (
    MAX_CONSECUTIVE_ERRORS,
    MAX_INVESTIGATION_MS,
    MAX_TOOL_CALLS,
    SupportHandoff,
    SupportResult,
    get_case_id,
    plan_support_step,
    render_control_result,
    update_case,
    update_handoff_identifiers,
)
from app.tools import TOOL_FUNCTIONS, execute_tool_batch
from app.trace import current_trace_id


class CustomerState(TypedDict, total=False):
    question: str
    auth: dict[str, str]
    conversation_id: str
    history: list[dict[str, object]]
    retrieval_mode: str
    plan: dict[str, object]
    plan_usage: dict[str, int | None]
    retrieved: list[dict[str, object]]
    answer: dict[str, object]
    trace_id: str | None


def plan_node(state: CustomerState) -> CustomerState:
    plan, usage = plan_customer_query(state["question"], state["history"])
    return {"plan": plan.model_dump(), "plan_usage": usage}


def route_plan(state: CustomerState) -> Literal["retrieve", "direct"]:
    return "retrieve" if state["plan"]["decision"] == "search" else "direct"


def direct_node(state: CustomerState) -> CustomerState:
    answer = no_search_answer(CustomerQueryPlan(**state["plan"]), state["plan_usage"])
    return {"answer": answer.model_dump(), "trace_id": current_trace_id()}


def retrieve_node(state: CustomerState) -> CustomerState:
    auth = AuthContext(**state["auth"])
    plan = CustomerQueryPlan(**state["plan"])
    chunks = retrieve_documents(plan.search_query, auth, state["conversation_id"], plan.version, plan.product, state["retrieval_mode"])
    return {"retrieved": [chunk.model_dump() for chunk in chunks]}


def answer_node(state: CustomerState) -> CustomerState:
    auth = AuthContext(**state["auth"])
    plan = CustomerQueryPlan(**state["plan"])
    chunks = [RetrievedChunk(**item) for item in state["retrieved"]]
    answer = answer_customer_question(state["question"], chunks, state["history"], auth, plan.version)
    answer.usage = add_usage(state["plan_usage"], answer.usage)
    return {"answer": answer.model_dump(), "trace_id": current_trace_id()}


def build_graph() -> StateGraph:
    graph = StateGraph(CustomerState)
    graph.add_node("plan", plan_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("answer", answer_node)
    graph.add_node("direct", direct_node)
    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", route_plan, {"retrieve": "retrieve", "direct": "direct"})
    graph.add_edge("retrieve", "answer")
    graph.add_edge("answer", END)
    graph.add_edge("direct", END)
    return graph


@traceable(name="customer_conversation_turn", run_type="chain")
def run_customer_graph(question: str, auth: AuthContext, conversation_id: str, history: list[dict[str, object]], retrieval_mode: str) -> tuple[CustomerAnswer, str | None]:
    settings = get_settings()
    with PostgresSaver.from_conn_string(settings.postgres_url) as checkpointer:
        checkpointer.setup()
        graph = build_graph().compile(checkpointer=checkpointer)
        result = graph.invoke(
            {"question": question, "auth": auth.model_dump(), "conversation_id": conversation_id, "history": history, "retrieval_mode": retrieval_mode},
            config={"configurable": {"thread_id": conversation_id, "checkpoint_ns": "customer"}},
        )
    return CustomerAnswer(**result["answer"]), result.get("trace_id")


class SupportState(TypedDict, total=False):
    question: str
    auth: dict[str, str]
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


def support_plan_node(state: SupportState) -> SupportState:
    import time

    handoff = SupportHandoff(**state["handoff"])
    evidence = [EvidenceRecord(**item) for item in state.get("evidence", [])]
    if handoff.missing_fields:
        labels = "、".join(handoff.missing_fields)
        return {"answer": f"继续调查前请补充：{labels}。", "status": "needs_info"}
    if len(evidence) >= MAX_TOOL_CALLS or int((time.perf_counter() - state["started_at"]) * 1000) >= MAX_INVESTIGATION_MS:
        return {"answer": "调查已达到本次预算上限，当前证据已保留，等待人工继续处理。", "status": "pending_human"}
    calls, usage, model_name = plan_support_step(state["question"], handoff, evidence, MAX_TOOL_CALLS - len(evidence))
    total_usage = add_usage(state.get("usage", {}), usage)
    models_used = [*state.get("models_used", []), model_name]
    read_calls = [call for call in calls if call["name"] in TOOL_FUNCTIONS]
    if read_calls:
        if len(read_calls) > MAX_TOOL_CALLS - len(evidence):
            return {"answer": "模型提出的检查超过工具预算，调查已安全停止并等待人工处理。", "status": "pending_human", "usage": total_usage, "models_used": models_used}
        previous = {(record.tool_name, json_key(record.request)) for record in evidence}
        new_calls = [call for call in read_calls if (str(call["name"]), json_key(dict(call.get("args") or {}))) not in previous]
        if not new_calls:
            return {"answer": "没有新的安全检查可执行，当前证据已保留，等待人工继续处理。", "status": "pending_human", "usage": total_usage, "models_used": models_used}
        return {"proposed_calls": new_calls, "usage": total_usage, "models_used": models_used}
    control_calls = [call for call in calls if call["name"] in {"FinishInvestigation", "RequestInformation", "EscalateInvestigation"}]
    if not control_calls:
        return {"answer": "模型没有返回允许的下一步，调查已安全停止。", "status": "pending_human", "usage": total_usage, "models_used": models_used}
    answer, status = render_control_result(control_calls[0], evidence)
    return {"answer": answer, "status": status, "usage": total_usage, "models_used": models_used}


def json_key(value: dict[str, object]) -> str:
    import json

    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def support_execute_node(state: SupportState) -> SupportState:
    auth = AuthContext(**state["auth"])
    handoff = SupportHandoff(**state["handoff"])
    evidence = [EvidenceRecord(**item) for item in state.get("evidence", [])]
    new_evidence = execute_tool_batch(state["case_id"], auth, state["proposed_calls"], handoff.known_shop_id or "", handoff.known_order_id or "")
    combined = [*evidence, *new_evidence]
    consecutive_errors = 0
    for record in reversed(combined):
        if record.status in {"forbidden", "unavailable", "error"}:
            consecutive_errors += 1
        else:
            break
    result: SupportState = {"evidence": [record.model_dump() for record in combined], "proposed_calls": []}
    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
        result.update({"answer": "连续内部检查失败，调查已安全停止并等待人工处理。", "status": "pending_human"})
    return result


def route_support(state: SupportState) -> Literal["execute", "plan", "done"]:
    if state.get("status"):
        return "done"
    if state.get("proposed_calls"):
        return "execute"
    return "plan"


def build_support_graph() -> StateGraph:
    graph = StateGraph(SupportState)
    graph.add_node("plan", support_plan_node)
    graph.add_node("execute", support_execute_node)
    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", route_support, {"execute": "execute", "plan": "plan", "done": END})
    graph.add_conditional_edges("execute", route_support, {"execute": "execute", "plan": "plan", "done": END})
    return graph


@traceable(name="support_conversation_turn", run_type="chain")
def run_support_graph(question: str, auth: AuthContext, conversation_id: str) -> SupportResult:
    import time

    settings = get_settings()
    started_at = time.perf_counter()
    handoff = update_handoff_identifiers(conversation_id, auth, question)
    case_id = get_case_id(conversation_id, auth)
    evidence = load_evidence(case_id)
    with PostgresSaver.from_conn_string(settings.postgres_url) as checkpointer:
        checkpointer.setup()
        graph = build_support_graph().compile(checkpointer=checkpointer)
        result = graph.invoke(
            {"question": question, "auth": auth.model_dump(), "conversation_id": conversation_id, "case_id": case_id, "handoff": handoff.model_dump(), "evidence": [record.model_dump() for record in evidence], "usage": {}, "models_used": [], "started_at": started_at},
            config={"configurable": {"thread_id": conversation_id, "checkpoint_ns": "support"}, "recursion_limit": 20},
        )
    final_evidence = [EvidenceRecord(**item) for item in result.get("evidence", [])]
    total_latency_ms = int((time.perf_counter() - started_at) * 1000)
    status = result.get("status", "pending_human")
    answer = result.get("answer", "调查未形成可核验结果，等待人工继续处理。")
    update_case(case_id, status, answer, len(final_evidence), total_latency_ms)
    return SupportResult(answer=answer, status=status, case_id=case_id, evidence_ids=[record.evidence_id for record in final_evidence], tool_path=[record.tool_name for record in final_evidence], tool_call_count=len(final_evidence), models_used=result.get("models_used", []), usage=result.get("usage", {}), trace_id=current_trace_id())
