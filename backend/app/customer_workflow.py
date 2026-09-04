from typing import TypedDict
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field

from app.customer_agent import ProblemDetails, create_customer_question, update_customer_problem


class CustomerSupportState(TypedDict):
    session_id: str
    customer_id: str
    customer_message: str
    messages: list[str]
    problem_details: ProblemDetails | None
    asked_questions: list[str]
    missing_information: list[str]
    turn_count: int
    customer_response: str | None
    status: str
    error: str | None


class SupportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=5000)


class CustomerMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=5000)


class SupportResponse(BaseModel):
    session_id: str
    problem_details: ProblemDetails
    customer_response: str
    status: str


def save_customer_message(state: CustomerSupportState) -> dict[str, object]:
    messages = state["messages"].copy()
    messages.append(f"Customer: {state['customer_message']}")

    if len(messages) > 12:
        messages = messages[-12:]

    return {"messages": messages, "turn_count": state["turn_count"] + 1}


def update_problem_details(state: CustomerSupportState) -> dict[str, object]:
    try:
        problem_details = update_customer_problem(state["messages"], state["problem_details"])
        return {"problem_details": problem_details, "missing_information": problem_details.missing_information}
    except Exception as error:
        return {"status": "error", "error": str(error)}


def choose_next_step(state: CustomerSupportState) -> str:
    if state["error"]:
        return "error"

    if not state["missing_information"]:
        return "ready_for_support"

    if state["turn_count"] > 3:
        return "needs_assistance"

    return "ask_for_information"


def ask_for_information(state: CustomerSupportState) -> dict[str, object]:
    problem_details = state["problem_details"]

    if problem_details is None:
        return {"status": "error", "error": "Problem details are missing"}

    try:
        customer_question = create_customer_question(problem_details, state["asked_questions"])
    except Exception as error:
        return {"status": "error", "error": str(error)}

    question_repeated = False

    for asked_question in state["asked_questions"]:
        if customer_question.strip().lower() == asked_question.strip().lower():
            question_repeated = True

    if question_repeated:
        customer_response = "I still do not have enough information, so I will ask the support team to continue checking this problem."
        messages = state["messages"].copy()
        messages.append(f"Agent: {customer_response}")
        return {"messages": messages, "customer_response": customer_response, "status": "needs_assistance"}

    asked_questions = state["asked_questions"].copy()
    asked_questions.append(customer_question)

    messages = state["messages"].copy()
    messages.append(f"Agent: {customer_question}")

    return {"messages": messages, "asked_questions": asked_questions, "customer_response": customer_question, "status": "waiting_for_customer"}


def ready_for_support(state: CustomerSupportState) -> dict[str, object]:
    customer_response = "Thanks. I now have enough information to continue checking this problem."

    messages = state["messages"].copy()
    messages.append(f"Agent: {customer_response}")

    return {"messages": messages, "customer_response": customer_response, "status": "ready_for_support"}


def needs_assistance(state: CustomerSupportState) -> dict[str, object]:
    customer_response = "Thanks. I still do not have enough information, so I will ask the support team to continue checking this problem."

    messages = state["messages"].copy()
    messages.append(f"Agent: {customer_response}")

    return {"messages": messages, "customer_response": customer_response, "status": "needs_assistance"}


customer_support_graph_builder = StateGraph(CustomerSupportState)
customer_support_graph_builder.add_node("save_customer_message", save_customer_message)
customer_support_graph_builder.add_node("update_problem_details", update_problem_details)
customer_support_graph_builder.add_node("ask_for_information", ask_for_information)
customer_support_graph_builder.add_node("ready_for_support", ready_for_support)
customer_support_graph_builder.add_node("needs_assistance", needs_assistance)

customer_support_graph_builder.add_edge(START, "save_customer_message")
customer_support_graph_builder.add_edge("save_customer_message", "update_problem_details")
customer_support_graph_builder.add_conditional_edges("update_problem_details", choose_next_step, {"ask_for_information": "ask_for_information", "ready_for_support": "ready_for_support", "needs_assistance": "needs_assistance", "error": END})
customer_support_graph_builder.add_edge("ask_for_information", END)
customer_support_graph_builder.add_edge("ready_for_support", END)
customer_support_graph_builder.add_edge("needs_assistance", END)

customer_support_graph = customer_support_graph_builder.compile(checkpointer=InMemorySaver())


def start_customer_support(customer_id: str, customer_message: str) -> SupportResponse:
    session_id = str(uuid4())

    initial_state: CustomerSupportState = {
        "session_id": session_id,
        "customer_id": customer_id,
        "customer_message": customer_message,
        "messages": [],
        "problem_details": None,
        "asked_questions": [],
        "missing_information": [],
        "turn_count": 0,
        "customer_response": None,
        "status": "started",
        "error": None,
    }

    config = {"configurable": {"thread_id": session_id}, "recursion_limit": 10}
    final_state = customer_support_graph.invoke(initial_state, config)

    if final_state["error"] is not None:
        raise RuntimeError(final_state["error"])

    return SupportResponse(session_id=session_id, problem_details=final_state["problem_details"], customer_response=final_state["customer_response"], status=final_state["status"])



def continue_customer_support(session_id: str, customer_message: str) -> SupportResponse:
    config = {"configurable": {"thread_id": session_id}, "recursion_limit": 10}
    saved_state = customer_support_graph.get_state(config)

    if not saved_state.values:
        raise ValueError("Support session not found")

    saved_values = saved_state.values

    current_state: CustomerSupportState = {
        "session_id": session_id,
        "customer_id": saved_values["customer_id"],
        "customer_message": customer_message,
        "messages": saved_values["messages"],
        "problem_details": saved_values["problem_details"],
        "asked_questions": saved_values["asked_questions"],
        "missing_information": saved_values["missing_information"],
        "turn_count": saved_values["turn_count"],
        "customer_response": None,
        "status": "started",
        "error": None,
    }

    final_state = customer_support_graph.invoke(current_state, config)

    if final_state["error"] is not None:
        raise RuntimeError(final_state["error"])

    return SupportResponse(session_id=session_id, problem_details=final_state["problem_details"], customer_response=final_state["customer_response"], status=final_state["status"])
