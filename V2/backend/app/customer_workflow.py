from typing import Literal, TypedDict

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langsmith import traceable

from backend.app.config import get_settings
from backend.app.customer_agent import build_non_search_answer, decide_customer_query_next_step, generate_answer_from_documents, sum_token_usage
from backend.app.customer_retrieval import retrieve_customer_documents
from backend.app.models import CustomerAnswer, CustomerQueryDecision, RetrievedChunk, UserContext
from backend.app.trace import current_trace_id


# 工作过程中先使用容易保存的普通数据，需要真正调用函数时再变回我们定义好的 Data Model
#数据库保存和恢复普通数据最稳，也更容易查看
class CustomerWorkflowState(TypedDict, total=False):
    question: str
    user: dict[str, str]
    conversation_id: str
    history: list[dict[str, object]]
    search_mode: str
    query_decision: dict[str, object]
    query_decision_usage: dict[str, int | None]
    retrieved_documents: list[dict[str, object]]
    answer: dict[str, object]
    trace_id: str | None


def decide_query_next_step_node(state: CustomerWorkflowState) -> CustomerWorkflowState:
    query_decision, usage = decide_customer_query_next_step(state["question"], state["history"])

    return {
        "query_decision": query_decision.model_dump(),
        "query_decision_usage": usage,
    }


def choose_next_step(state: CustomerWorkflowState) -> Literal["search_documents", "build_non_search_answer"]:
    if state["query_decision"]["decision"] == "search":
        return "search_documents"

    return "build_non_search_answer"


def build_non_search_answer_node(state: CustomerWorkflowState) -> CustomerWorkflowState:
    query_decision = CustomerQueryDecision.model_validate(state["query_decision"])
    answer = build_non_search_answer(query_decision, state["query_decision_usage"])

    return {
        "answer": answer.model_dump(),
        "trace_id": current_trace_id(),
    }


def search_documents_node(state: CustomerWorkflowState) -> CustomerWorkflowState:
    user = UserContext.model_validate(state["user"])
    query_decision = CustomerQueryDecision.model_validate(state["query_decision"])

    chunks = retrieve_customer_documents(
        query_decision.search_query,
        user,
        state["conversation_id"],
        query_decision.version,
        query_decision.product,
        state["search_mode"],
    )

    retrieved_documents: list[dict[str, object]] = []

    for chunk in chunks:
        retrieved_documents.append(chunk.model_dump())

    return {
        "retrieved_documents": retrieved_documents,
    }


def generate_answer_node(state: CustomerWorkflowState) -> CustomerWorkflowState:
    user = UserContext.model_validate(state["user"])
    query_decision = CustomerQueryDecision.model_validate(state["query_decision"])

    chunks: list[RetrievedChunk] = []

    for item in state["retrieved_documents"]:
        chunks.append(RetrievedChunk.model_validate(item))

    answer = generate_answer_from_documents(
        state["question"],
        chunks,
        state["history"],
        user,
        query_decision.version,
    )

    answer.usage = sum_token_usage(
        state["query_decision_usage"],
        answer.usage,
    )

    return {
        "answer": answer.model_dump(),
        "trace_id": current_trace_id(),
    }


def build_customer_workflow() -> StateGraph:
    workflow = StateGraph(CustomerWorkflowState)

    workflow.add_node("decide_query_next_step", decide_query_next_step_node)
    workflow.add_node("search_documents", search_documents_node)
    workflow.add_node("generate_answer", generate_answer_node)
    workflow.add_node("build_non_search_answer", build_non_search_answer_node)

    workflow.add_edge(START, "decide_query_next_step")

    workflow.add_conditional_edges(
        "decide_query_next_step",
        choose_next_step,
        {
            "search_documents": "search_documents",
            "build_non_search_answer": "build_non_search_answer",
        },
    )

    workflow.add_edge("search_documents", "generate_answer")
    workflow.add_edge("generate_answer", END)
    workflow.add_edge("build_non_search_answer", END)

    return workflow


@traceable(name="customer_conversation_turn", run_type="chain")
def run_customer_workflow(question: str, user: UserContext, conversation_id: str, history: list[dict[str, object]], retrieval_mode: str) -> tuple[CustomerAnswer, str | None]:
    settings = get_settings()

    with PostgresSaver.from_conn_string(settings.postgres_url) as checkpointer:
        checkpointer.setup()

        workflow_builder = build_customer_workflow()
        workflow = workflow_builder.compile(checkpointer=checkpointer)

        initial_state: CustomerWorkflowState = {
            "question": question,
            "user": user.model_dump(),
            "conversation_id": conversation_id,
            "history": history,
            "search_mode": retrieval_mode,
        }

        workflow_config = {
            "configurable": {
                "thread_id": conversation_id,
                "checkpoint_ns": "customer",
            }
        }

        result = workflow.invoke(initial_state, config=workflow_config)

    return CustomerAnswer.model_validate(result["answer"]), result.get("trace_id")


# START
# ↓
# decide_query_next_step    生成 query_decision
# ↓
# choose_next_step    判断是不是需要搜索
# ├── search
# │   ↓
# │   search_documents
# │   ↓
# │   generate_answer
# │   ↓
# │   END
# │
# └── clarify / handoff
#     ↓
#     build_non_search_answer
#     ↓
#     END