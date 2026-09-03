from typing import TypedDict
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field

from app.customer_agent import ProblemDetails, understand_customer_problem


class CustomerSupportState(TypedDict):
    session_id: str
    customer_message: str
    problem_details: ProblemDetails | None
    customer_response: str | None
    status: str
    error: str | None


class SupportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=5000)


class SupportResponse(BaseModel):
    session_id: str
    problem_details: ProblemDetails
    customer_response: str
    status: str


def understand_problem(state: CustomerSupportState) -> dict[str, object]:
    try:
        problem_details = understand_customer_problem(state["customer_message"])
        return {"problem_details": problem_details}
    except Exception as error:
        return {"status": "error", "error": str(error)}


def write_customer_response(state: CustomerSupportState) -> dict[str, object]:
    if state["error"]:
        return {"status": "error"}

    problem_details = state["problem_details"]

    if problem_details is None:
        return {"status": "error", "error": "Problem details are missing"}

    customer_response = f"Thanks. I understand the problem as: {problem_details.summary} I have saved these details and can continue helping you."
    return {"customer_response": customer_response, "status": "understood"}


customer_support_graph_builder = StateGraph(CustomerSupportState)
customer_support_graph_builder.add_node("understand_problem", understand_problem)
customer_support_graph_builder.add_node("write_customer_response", write_customer_response)
customer_support_graph_builder.add_edge(START, "understand_problem")
customer_support_graph_builder.add_edge("understand_problem", "write_customer_response")
customer_support_graph_builder.add_edge("write_customer_response", END)

customer_support_graph = customer_support_graph_builder.compile(checkpointer=InMemorySaver())


def start_customer_support(customer_id: str, customer_message: str) -> SupportResponse:
    session_id = str(uuid4())

    initial_state: CustomerSupportState = {
        "session_id": session_id,
        "customer_message": customer_message,
        "problem_details": None,
        "customer_response": None,
        "status": "started",
        "error": None,
    }

    config = {"configurable": {"thread_id": session_id}, "recursion_limit": 5}
    final_state = customer_support_graph.invoke(initial_state, config)

    if final_state["error"] is not None:
        raise RuntimeError(final_state["error"])

    return SupportResponse(session_id=session_id, problem_details=final_state["problem_details"], customer_response=final_state["customer_response"], status=final_state["status"])
