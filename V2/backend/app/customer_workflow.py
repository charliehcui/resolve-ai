from typing import Literal, TypedDict

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langsmith import traceable

from backend.app.config import get_settings
from backend.app.customer_agent import add_usage, answer_customer_question, no_search_answer, plan_customer_query
from backend.app.customer_retrieval import retrieve_customer_documents
from backend.app.models import AuthContext, CustomerAnswer, CustomerQueryPlan, RetrievedChunk
from backend.app.trace import current_trace_id


class CustomerGraphState(TypedDict, total=False):
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


def plan_node(state: CustomerGraphState) -> CustomerGraphState:
    plan, usage = plan_customer_query(state["question"], state["history"])
    return {"plan": plan.model_dump(), "plan_usage": usage}


def route_plan(state: CustomerGraphState) -> Literal["retrieve", "direct"]:
    if state["plan"]["decision"] == "search":
        return "retrieve"

    return "direct"


def direct_node(state: CustomerGraphState) -> CustomerGraphState:
    plan = CustomerQueryPlan(**state["plan"])
    answer = no_search_answer(plan, state["plan_usage"])
    return {"answer": answer.model_dump(), "trace_id": current_trace_id()}


def retrieve_node(state: CustomerGraphState) -> CustomerGraphState:
    auth = AuthContext(**state["auth"])
    plan = CustomerQueryPlan(**state["plan"])
    chunks = retrieve_customer_documents(plan.search_query, auth, state["conversation_id"], plan.version, plan.product, state["retrieval_mode"])

    retrieved_documents: list[dict[str, object]] = []
    for chunk in chunks:
        retrieved_documents.append(chunk.model_dump())

    return {"retrieved": retrieved_documents}


def answer_node(state: CustomerGraphState) -> CustomerGraphState:
    auth = AuthContext(**state["auth"])
    plan = CustomerQueryPlan(**state["plan"])
    chunks: list[RetrievedChunk] = []
    for item in state["retrieved"]:
        chunks.append(RetrievedChunk(**item))

    answer = answer_customer_question(state["question"], chunks, state["history"], auth, plan.version)
    answer.usage = add_usage(state["plan_usage"], answer.usage)
    return {"answer": answer.model_dump(), "trace_id": current_trace_id()}


def build_customer_graph() -> StateGraph:
    graph = StateGraph(CustomerGraphState)
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
        graph_builder = build_customer_graph()
        graph = graph_builder.compile(checkpointer=checkpointer)

        initial_state: CustomerGraphState = {
            "question": question,
            "auth": auth.model_dump(),
            "conversation_id": conversation_id,
            "history": history,
            "retrieval_mode": retrieval_mode,
        }
        graph_config = {
            "configurable": {
                "thread_id": conversation_id,
                "checkpoint_ns": "customer",
            }
        }
        result = graph.invoke(initial_state, config=graph_config)

    return CustomerAnswer(**result["answer"]), result.get("trace_id")


