from typing import TypedDict
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field

from app.customer_agent import CustomerResolution, CustomerVerification, ProblemDetails, create_customer_question, create_customer_resolution_from_documents, should_get_recent_customer_activity, update_customer_problem, update_customer_problem_with_customer_side_data, verify_customer_resolution
from app.customer_question_retrieval import retrieve_documents_for_customer_question
from app.customer_tools import get_current_product_context, get_recent_customer_activity


class CustomerDocumentCitation(BaseModel):
    chunk_id: str
    source_uri: str
    version: str


class CustomerSupportState(TypedDict):
    session_id: str
    customer_id: str
    customer_message: str
    messages: list[str]
    problem_details: ProblemDetails | None
    customer_side_data: dict[str, object]
    verification_customer_side_data: dict[str, object]
    asked_questions: list[str]
    missing_information: list[str]
    retrieved_customer_documents: list[dict[str, object]]
    citations: list[dict[str, object]]
    resolution: CustomerResolution | None
    verification_result: CustomerVerification | None
    verification_source: str | None
    turn_count: int
    customer_response: str | None
    status: str
    error: str | None


class SupportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    customer_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=5000)


class CustomerMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=5000)


class SupportResponse(BaseModel):
    session_id: str
    problem_details: ProblemDetails
    customer_response: str
    customer_facts: list[str]
    citations: list[CustomerDocumentCitation]
    resolution: CustomerResolution | None
    verification_result: CustomerVerification | None
    verification_source: str | None
    status: str


def save_customer_message(state: CustomerSupportState) -> dict[str, object]:
    messages = state["messages"].copy()
    messages.append(f"Customer: {state['customer_message']}")

    return {"messages": messages[-12:], "turn_count": state["turn_count"] + 1}


def choose_step_after_message(state: CustomerSupportState) -> str:
    if state["resolution"] is not None:
        return "wait_for_customer_verification"

    return "update_problem_details"


def update_problem_details(state: CustomerSupportState) -> dict[str, object]:
    try:
        problem_details = update_customer_problem(state["messages"], state["problem_details"])
        return {"problem_details": problem_details, "missing_information": problem_details.missing_information}
    except Exception as error:
        return {"status": "error", "error": str(error)}


def get_customer_side_data(state: CustomerSupportState) -> dict[str, object]:
    if state["error"] is not None:
        return {}

    problem_details = state["problem_details"]

    if problem_details is None:
        return {"status": "error", "error": "Problem details are missing"}

    try:
        current_product_context = get_current_product_context(state["customer_id"])
        customer_side_data = {"current_product_context": current_product_context}

        needs_recent_activity = should_get_recent_customer_activity(problem_details, current_product_context)

        if needs_recent_activity is True:
            recent_activity = get_recent_customer_activity(state["customer_id"])
            customer_side_data["recent_activity"] = recent_activity

        return {"customer_side_data": customer_side_data}
    except Exception as error:
        return {"status": "error", "error": str(error)}


def update_problem_with_customer_side_data(state: CustomerSupportState) -> dict[str, object]:
    if state["error"] is not None:
        return {}

    problem_details = state["problem_details"]

    if problem_details is None:
        return {"status": "error", "error": "Problem details are missing"}

    try:
        updated_problem_details = update_customer_problem_with_customer_side_data(problem_details, state["customer_side_data"])
        return {"problem_details": updated_problem_details, "missing_information": updated_problem_details.missing_information}
    except Exception as error:
        return {"status": "error", "error": str(error)}


def choose_next_step(state: CustomerSupportState) -> str:
    if state["error"] is not None:
        return "error"

    if len(state["missing_information"]) == 0:
        return "retrieve_customer_documents"

    if len(state["asked_questions"]) >= 3:
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

    for asked_question in state["asked_questions"]:
        if customer_question.strip().lower() == asked_question.strip().lower():
            return needs_assistance(state)

    asked_questions = state["asked_questions"].copy()
    asked_questions.append(customer_question)

    messages = state["messages"].copy()
    messages.append(f"Agent: {customer_question}")

    return {"messages": messages[-12:], "asked_questions": asked_questions, "customer_response": customer_question, "status": "waiting_for_customer"}


def retrieve_customer_documents(state: CustomerSupportState) -> dict[str, object]:
    problem_details = state["problem_details"]

    if problem_details is None:
        return {"status": "error", "error": "Problem details are missing"}

    current_product_context = state["customer_side_data"]["current_product_context"]
    version = current_product_context.get("product_version")
    customer_question = f"{problem_details.summary}\nAffected feature: {problem_details.affected_feature}\n{problem_details.problem}\nCustomer goal: {problem_details.customer_goal}"

    try:
        retrieved_customer_documents = retrieve_documents_for_customer_question.invoke({"customer_question": customer_question, "version": version})
        return {"retrieved_customer_documents": retrieved_customer_documents}
    except Exception as error:
        return {"status": "error", "error": str(error)}


def choose_step_after_document_retrieval(state: CustomerSupportState) -> str:
    if state["error"] is not None:
        return "error"

    if len(state["retrieved_customer_documents"]) == 0:
        return "needs_assistance"

    return "prepare_customer_resolution"


def needs_assistance(state: CustomerSupportState) -> dict[str, object]:
    customer_response = "I cannot safely suggest a solution with the available information. Please contact technical support for further help."

    messages = state["messages"].copy()
    messages.append(f"Agent: {customer_response}")

    return {"messages": messages[-12:], "customer_response": customer_response, "resolution": None, "citations": [], "status": "needs_assistance"}


def prepare_customer_resolution(state: CustomerSupportState) -> dict[str, object]:
    problem_details = state["problem_details"]

    if problem_details is None:
        return {"status": "error", "error": "Problem details are missing"}

    try:
        resolution = create_customer_resolution_from_documents(problem_details, state["customer_side_data"], state["retrieved_customer_documents"])
    except Exception as error:
        return {"status": "error", "error": str(error)}

    if resolution.can_resolve is False:
        return {"resolution": None, "citations": []}

    if len(resolution.steps) == 0 or len(resolution.citation_ids) == 0:
        return {"resolution": None, "citations": []}

    for step in resolution.steps:
        if not step.strip():
            return {"resolution": None, "citations": []}

    documents_by_id = {}

    for document in state["retrieved_customer_documents"]:
        documents_by_id[str(document["chunk_id"])] = document

    current_product_context = state["customer_side_data"]["current_product_context"]
    version = current_product_context.get("product_version")
    citations: list[dict[str, object]] = []
    added_ids: list[str] = []

    for chunk_id in resolution.citation_ids:
        document = documents_by_id.get(chunk_id)

        if document is None:
            return {"resolution": None, "citations": []}

        if version is not None and str(document["version"]) != str(version):
            return {"resolution": None, "citations": []}

        if chunk_id not in added_ids:
            citation = {"chunk_id": chunk_id, "source_uri": str(document["source_uri"]), "version": str(document["version"])}
            citations.append(citation)
            added_ids.append(chunk_id)

    return {"resolution": resolution, "citations": citations}


def choose_step_after_resolution(state: CustomerSupportState) -> str:
    if state["error"] is not None:
        return "error"

    if state["resolution"] is None:
        return "needs_assistance"

    return "offer_customer_resolution"


def offer_customer_resolution(state: CustomerSupportState) -> dict[str, object]:
    resolution = state["resolution"]

    if resolution is None:
        return needs_assistance(state)

    response_parts = [resolution.explanation, ""]

    for step_number, step in enumerate(resolution.steps, start=1):
        response_parts.append(f"{step_number}. {step}")

    response_parts.append("")
    response_parts.append("After trying these steps, please tell me whether the problem is fixed.")
    customer_response = "\n".join(response_parts)

    messages = state["messages"].copy()
    messages.append(f"Agent: {customer_response}")

    return {"messages": messages[-12:], "customer_response": customer_response, "verification_result": None, "verification_source": None, "verification_customer_side_data": {}, "status": "waiting_for_verification"}


def wait_for_customer_verification(state: CustomerSupportState) -> dict[str, object]:
    problem_details = state["problem_details"]
    resolution = state["resolution"]

    if problem_details is None or resolution is None:
        return {"error": "The original problem or resolution is missing"}

    try:
        verification_result = verify_customer_resolution(problem_details, resolution, state["customer_message"])
        return {"verification_result": verification_result, "verification_source": None}
    except Exception as error:
        return {"error": str(error)}


def choose_step_after_customer_verification(state: CustomerSupportState) -> str:
    if state["error"] is not None:
        return "error"

    verification_result = state["verification_result"]

    if verification_result is None:
        return "error"

    if verification_result.result == "unclear":
        return "verify_resolution_with_tools"

    return "finalize_customer_resolution"


def tool_data_confirms_recovery(original_customer_side_data: dict[str, object], verification_customer_side_data: dict[str, object]) -> bool:
    original_context = original_customer_side_data.get("current_product_context")
    verification_context = verification_customer_side_data.get("current_product_context")

    if isinstance(original_context, dict) and isinstance(verification_context, dict):
        original_feature_enabled = original_context.get("feature_enabled")
        verification_feature_enabled = verification_context.get("feature_enabled")

        if original_feature_enabled is False and verification_feature_enabled is True:
            return True

        original_account_status = original_context.get("account_status")
        verification_account_status = verification_context.get("account_status")

        if isinstance(original_account_status, str) and isinstance(verification_account_status, str):
            if original_account_status.strip().lower() != "active" and verification_account_status.strip().lower() == "active":
                return True

    original_recent_activity = original_customer_side_data.get("recent_activity")
    verification_recent_activity = verification_customer_side_data.get("recent_activity")

    if isinstance(original_recent_activity, dict) and isinstance(verification_recent_activity, dict):
        original_occurred_at = original_recent_activity.get("occurred_at")
        verification_occurred_at = verification_recent_activity.get("occurred_at")
        verification_result = verification_recent_activity.get("result")

        if original_occurred_at != verification_occurred_at and isinstance(verification_result, str):
            normalized_result = verification_result.strip().lower()

            if normalized_result in ("success", "successful", "succeeded", "completed", "delivered", "sent", "ok"):
                return True

    return False


def verify_resolution_with_tools(state: CustomerSupportState) -> dict[str, object]:
    try:
        current_product_context = get_current_product_context(state["customer_id"])
        verification_customer_side_data = {"current_product_context": current_product_context}

        if "recent_activity" in state["customer_side_data"]:
            recent_activity = get_recent_customer_activity(state["customer_id"])
            verification_customer_side_data["recent_activity"] = recent_activity
    except Exception:
        return {"verification_customer_side_data": {}, "verification_source": None}

    if tool_data_confirms_recovery(state["customer_side_data"], verification_customer_side_data) is True:
        verification_result = CustomerVerification(result="resolved", supporting_text="")
        return {"verification_customer_side_data": verification_customer_side_data, "verification_result": verification_result, "verification_source": "tool_verification"}

    return {"verification_customer_side_data": verification_customer_side_data, "verification_source": None}


def finalize_customer_resolution(state: CustomerSupportState) -> dict[str, object]:
    if state["error"] is not None:
        return {}

    verification_result = state["verification_result"]

    if verification_result is None:
        return {"error": "Customer verification is missing"}

    verification_source = state["verification_source"]

    if verification_result.result == "resolved" and verification_source == "tool_verification":
        status = "resolved"
        customer_response = "The system confirmed that the relevant customer state changed successfully. This support session is complete."
    elif verification_result.result == "resolved":
        status = "resolved"
        verification_source = "customer_confirmation"
        customer_response = "You confirmed that the problem is fixed. This support session is complete."
    elif verification_result.result == "unresolved":
        status = "unresolved"
        verification_source = "customer_confirmation"
        customer_response = "You confirmed that the problem still happens. Please stop repeating these steps and contact technical support for further help."
    else:
        status = "waiting_for_verification"
        verification_source = None
        customer_response = "I have not recorded the problem as fixed. After trying the suggested steps, is the original problem still happening?"

    messages = state["messages"].copy()
    messages.append(f"Agent: {customer_response}")

    return {"messages": messages[-12:], "customer_response": customer_response, "verification_source": verification_source, "status": status}


customer_support_graph_builder = StateGraph(CustomerSupportState)
customer_support_graph_builder.add_node("save_customer_message", save_customer_message)
customer_support_graph_builder.add_node("update_problem_details", update_problem_details)
customer_support_graph_builder.add_node("get_customer_side_data", get_customer_side_data)
customer_support_graph_builder.add_node("update_problem_with_customer_side_data", update_problem_with_customer_side_data)
customer_support_graph_builder.add_node("ask_for_information", ask_for_information)
customer_support_graph_builder.add_node("retrieve_customer_documents", retrieve_customer_documents)
customer_support_graph_builder.add_node("prepare_customer_resolution", prepare_customer_resolution)
customer_support_graph_builder.add_node("offer_customer_resolution", offer_customer_resolution)
customer_support_graph_builder.add_node("wait_for_customer_verification", wait_for_customer_verification)
customer_support_graph_builder.add_node("verify_resolution_with_tools", verify_resolution_with_tools)
customer_support_graph_builder.add_node("finalize_customer_resolution", finalize_customer_resolution)
customer_support_graph_builder.add_node("needs_assistance", needs_assistance)

customer_support_graph_builder.add_edge(START, "save_customer_message")
customer_support_graph_builder.add_conditional_edges("save_customer_message", choose_step_after_message, {"update_problem_details": "update_problem_details", "wait_for_customer_verification": "wait_for_customer_verification"})
customer_support_graph_builder.add_edge("update_problem_details", "get_customer_side_data")
customer_support_graph_builder.add_edge("get_customer_side_data", "update_problem_with_customer_side_data")
customer_support_graph_builder.add_conditional_edges("update_problem_with_customer_side_data", choose_next_step, {"ask_for_information": "ask_for_information", "retrieve_customer_documents": "retrieve_customer_documents", "needs_assistance": "needs_assistance", "error": END})
customer_support_graph_builder.add_conditional_edges("retrieve_customer_documents", choose_step_after_document_retrieval, {"prepare_customer_resolution": "prepare_customer_resolution", "needs_assistance": "needs_assistance", "error": END})
customer_support_graph_builder.add_conditional_edges("prepare_customer_resolution", choose_step_after_resolution, {"offer_customer_resolution": "offer_customer_resolution", "needs_assistance": "needs_assistance", "error": END})
customer_support_graph_builder.add_edge("ask_for_information", END)
customer_support_graph_builder.add_edge("offer_customer_resolution", END)
customer_support_graph_builder.add_edge("needs_assistance", END)
customer_support_graph_builder.add_conditional_edges("wait_for_customer_verification", choose_step_after_customer_verification, {"verify_resolution_with_tools": "verify_resolution_with_tools", "finalize_customer_resolution": "finalize_customer_resolution", "error": END})
customer_support_graph_builder.add_edge("verify_resolution_with_tools", "finalize_customer_resolution")
customer_support_graph_builder.add_edge("finalize_customer_resolution", END)

customer_support_graph = customer_support_graph_builder.compile(checkpointer=InMemorySaver())


def build_support_response(state: CustomerSupportState) -> SupportResponse:
    if state["error"] is not None:
        raise RuntimeError(state["error"])

    if state["problem_details"] is None or state["customer_response"] is None:
        raise RuntimeError("Customer support did not produce a response")

    customer_facts: list[str] = []
    customer_side_data = state["customer_side_data"]

    if len(state["verification_customer_side_data"]) > 0:
        customer_side_data = state["verification_customer_side_data"]

    current_product_context = customer_side_data.get("current_product_context")

    if isinstance(current_product_context, dict):
        account_status = current_product_context.get("account_status")
        product_version = current_product_context.get("product_version")
        affected_feature = current_product_context.get("affected_feature")
        feature_enabled = current_product_context.get("feature_enabled")

        if isinstance(account_status, str):
            customer_facts.append(f"Account status: {account_status}.")

        if isinstance(product_version, str):
            customer_facts.append(f"Product version: {product_version}.")

        if isinstance(affected_feature, str) and isinstance(feature_enabled, bool):
            feature_status = "disabled"

            if feature_enabled:
                feature_status = "enabled"

            customer_facts.append(f"{affected_feature.capitalize()}: {feature_status}.")

    recent_activity = customer_side_data.get("recent_activity")

    if isinstance(recent_activity, dict):
        activity = recent_activity.get("activity")
        result = recent_activity.get("result")
        occurred_at = recent_activity.get("occurred_at")

        if isinstance(activity, str) and isinstance(result, str) and isinstance(occurred_at, str):
            customer_facts.append(f"Recorded activity: {activity}. Result: {result}. Recorded at: {occurred_at}.")

    return SupportResponse(session_id=state["session_id"], problem_details=state["problem_details"], customer_response=state["customer_response"], customer_facts=customer_facts, citations=state["citations"], resolution=state["resolution"], verification_result=state["verification_result"], verification_source=state["verification_source"], status=state["status"])


def start_customer_support(customer_id: str, customer_message: str) -> SupportResponse:
    session_id = str(uuid4())

    initial_state: CustomerSupportState = {
        "session_id": session_id,
        "customer_id": customer_id,
        "customer_message": customer_message,
        "messages": [],
        "problem_details": None,
        "customer_side_data": {},
        "verification_customer_side_data": {},
        "asked_questions": [],
        "missing_information": [],
        "retrieved_customer_documents": [],
        "citations": [],
        "resolution": None,
        "verification_result": None,
        "verification_source": None,
        "turn_count": 0,
        "customer_response": None,
        "status": "started",
        "error": None,
    }

    config = {"configurable": {"thread_id": session_id}, "recursion_limit": 16}
    final_state = customer_support_graph.invoke(initial_state, config)

    return build_support_response(final_state)


def continue_customer_support(session_id: str, customer_message: str) -> SupportResponse:
    config = {"configurable": {"thread_id": session_id}, "recursion_limit": 16}
    saved_state = customer_support_graph.get_state(config)

    if len(saved_state.values) == 0:
        raise ValueError("Support session not found")

    if saved_state.values["status"] in ("resolved", "unresolved", "needs_assistance"):
        return build_support_response(saved_state.values)

    final_state = customer_support_graph.invoke({"customer_message": customer_message, "error": None}, config)

    return build_support_response(final_state)
