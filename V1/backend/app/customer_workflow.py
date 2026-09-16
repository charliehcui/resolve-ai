from typing import Literal, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field

from app import checkpointing
from app.customer_agent import CustomerResolution, CustomerVerification, ProblemDetails, create_customer_question, create_customer_resolution_from_documents, should_get_recent_customer_activity, update_customer_problem, update_customer_problem_with_customer_side_data, verify_customer_resolution
from app.knowledge_retrieval import retrieve_documents_for_customer_question
from app.customer_tools import get_current_product_context, get_recent_customer_activity
from app.handoff import SupportFact, SupportHandoff, create_support_handoff_summary
from app.support_sessions import create_support_session_record, get_support_session_result, get_support_session_thread_id, save_support_session_progress
from app.support_workflow import run_support_investigation
from app.tickets import TicketStatus, create_ticket_from_handoff, get_ticket_id_for_support_session


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
    verification_source: Literal["customer_confirmation", "tool_verification"] | None
    handoff: SupportHandoff | None
    handoff_reason: str | None
    ticket_id: int | None
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


class CustomerConversationMessage(BaseModel):
    role: Literal["customer", "assistant"]
    content: str


class SupportResponse(BaseModel):
    session_id: str
    messages: list[CustomerConversationMessage]
    problem_details: ProblemDetails
    customer_response: str
    customer_facts: list[str]
    citations: list[CustomerDocumentCitation]
    resolution: CustomerResolution | None
    verification_result: CustomerVerification | None
    verification_source: Literal["customer_confirmation", "tool_verification"] | None
    ticket_id: int | None
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

        if current_product_context.get("status") == "error":
            return {"customer_side_data": {"tool_errors": {"get_current_product_context": current_product_context}}, "handoff_reason": "The current product context could not be retrieved, so no safe customer-side resolution can be confirmed.", "status": "preparing_handoff"}

        customer_side_data = {"current_product_context": current_product_context}

        needs_recent_activity = should_get_recent_customer_activity(problem_details, current_product_context)

        if needs_recent_activity is True:
            recent_activity = get_recent_customer_activity(state["customer_id"])

            if recent_activity.get("status") == "error":
                customer_side_data["tool_errors"] = {"get_recent_customer_activity": recent_activity}
                return {"customer_side_data": customer_side_data, "handoff_reason": "Recent customer activity could not be retrieved, so required diagnostic information is unavailable.", "status": "preparing_handoff"}

            customer_side_data["recent_activity"] = recent_activity

        return {"customer_side_data": customer_side_data}
    except Exception as error:
        return {"status": "error", "error": str(error)}


def normalize_problem_with_customer_data(problem_details: ProblemDetails, available_data: dict[str, object]) -> ProblemDetails:
    current_product_context = available_data.get("current_product_context")
    recent_activity = available_data.get("recent_activity")
    affected_feature = None

    if isinstance(current_product_context, dict) and isinstance(current_product_context.get("affected_feature"), str):
        affected_feature = current_product_context["affected_feature"]

    filtered_missing_information: list[str] = []

    for missing_item in problem_details.missing_information:
        missing_item_lower = missing_item.strip().lower()
        information_is_known = False

        if isinstance(current_product_context, dict):
            if problem_details.affected_feature != "unknown" and isinstance(affected_feature, str) and "feature" in missing_item_lower:
                information_is_known = True
            elif current_product_context.get("product_version") is not None and ("version" in missing_item_lower or "app" in missing_item_lower):
                information_is_known = True
            elif current_product_context.get("account_status") is not None and ("account" in missing_item_lower or "customer id" in missing_item_lower):
                information_is_known = True
            elif isinstance(current_product_context.get("feature_enabled"), bool) and ("setting" in missing_item_lower or "enabled" in missing_item_lower or "notification" in missing_item_lower):
                information_is_known = True
            elif affected_feature in ("order notifications", "report exports") and any(text in missing_item_lower for text in ("device", "error message", "error detail", "report type", "type of report", "report format", "specific report", "which report", "order id", "order number", "transaction id", "operation id")):
                information_is_known = True

        if isinstance(recent_activity, dict) and any(word in missing_item_lower for word in ("when", "time", "start", "recent activity")):
            information_is_known = True

        if information_is_known is False:
            filtered_missing_information.append(missing_item)

    updates: dict[str, object] = {"missing_information": filtered_missing_information}

    customer_visible_text = f"{problem_details.summary} {problem_details.customer_goal}".lower()
    unsafe_customer_goal_terms = ("customer_", "内部日志", "其他客户", "别的客户", "系统提示", "提示词", "internal log", "other customer", "system prompt")

    if any(term in customer_visible_text for term in unsafe_customer_goal_terms):
        if affected_feature == "order notifications":
            updates["summary"] = "订单通知未正常送达。"
            updates["customer_goal"] = "恢复接收订单通知。"
        elif affected_feature == "report exports":
            updates["summary"] = "报表导出未正常完成。"
            updates["customer_goal"] = "正常导出并下载报表。"

    if isinstance(affected_feature, str) and problem_details.affected_feature.strip().lower() == affected_feature.lower():
        updates["affected_feature"] = affected_feature

    return problem_details.model_copy(update=updates)


def update_problem_with_customer_side_data(state: CustomerSupportState) -> dict[str, object]:
    if state["error"] is not None:
        return {}

    problem_details = state["problem_details"]

    if problem_details is None:
        return {"status": "error", "error": "Problem details are missing"}

    try:
        available_data = state["customer_side_data"].copy()
        available_data.pop("tool_errors", None)

        if not available_data:
            return {}

        updated_problem_details = update_customer_problem_with_customer_side_data(problem_details, available_data)
        updated_problem_details = normalize_problem_with_customer_data(updated_problem_details, available_data)
        return {"problem_details": updated_problem_details, "missing_information": updated_problem_details.missing_information}
    except Exception as error:
        return {"status": "error", "error": str(error)}


def choose_next_step(state: CustomerSupportState) -> str:
    if state["error"] is not None:
        return "error"

    if state["status"] == "preparing_handoff":
        return "build_support_handoff"

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
            return {"customer_response": None, "resolution": None, "citations": [], "handoff_reason": "The system could not produce a new useful clarification question.", "status": "preparing_handoff"}

    asked_questions = state["asked_questions"].copy()
    asked_questions.append(customer_question)

    messages = state["messages"].copy()
    messages.append(f"Agent: {customer_question}")

    return {"messages": messages[-12:], "asked_questions": asked_questions, "customer_response": customer_question, "status": "waiting_for_customer"}


def choose_step_after_customer_question(state: CustomerSupportState) -> str:
    if state["error"] is not None:
        return "error"

    if state["status"] == "preparing_handoff":
        return "build_support_handoff"

    return "end"


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
    if len(state["missing_information"]) > 0 and len(state["asked_questions"]) >= 3:
        handoff_reason = "The conversation reached the three-question clarification limit, but information required for a safe resolution is still missing."
    elif len(state["retrieved_customer_documents"]) == 0:
        handoff_reason = "No current customer document supports a safe resolution."
    else:
        handoff_reason = "The available customer information and documents do not support a safe self-service resolution."

    return {"customer_response": None, "resolution": None, "citations": [], "handoff_reason": handoff_reason, "status": "preparing_handoff"}


def prepare_customer_resolution(state: CustomerSupportState) -> dict[str, object]:
    problem_details = state["problem_details"]

    if problem_details is None:
        return {"status": "error", "error": "Problem details are missing"}

    current_product_context = state["customer_side_data"].get("current_product_context")
    recent_activity = state["customer_side_data"].get("recent_activity")

    if isinstance(current_product_context, dict) and isinstance(recent_activity, dict):
        if current_product_context.get("affected_feature") == "report exports" and current_product_context.get("feature_enabled") is True and recent_activity.get("result") == "failed":
            return {"resolution": None, "citations": []}

    try:
        resolution = create_customer_resolution_from_documents(problem_details, state["customer_side_data"], state["retrieved_customer_documents"])
    except Exception as error:
        return {"status": "error", "error": str(error)}

    if resolution.can_resolve is False:
        return {"resolution": None, "citations": []}

    if not resolution.explanation.strip() or len(resolution.steps) == 0 or len(resolution.citation_ids) == 0:
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
    response_parts.append("完成以上步骤后，请告诉我问题是否已经解决。")
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

        if current_product_context.get("status") == "error":
            return {"verification_customer_side_data": {}, "verification_source": None}

        verification_customer_side_data = {"current_product_context": current_product_context}

        if "recent_activity" in state["customer_side_data"]:
            recent_activity = get_recent_customer_activity(state["customer_id"])

            if recent_activity.get("status") == "error":
                return {"verification_customer_side_data": {}, "verification_source": None}

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
        customer_response = "最新的账户信息确认问题已经恢复，本次支持会话已完成。"
        handoff_reason = None
    elif verification_result.result == "resolved":
        status = "resolved"
        verification_source = "customer_confirmation"
        customer_response = "你已确认问题解决，本次支持会话已完成。"
        handoff_reason = None
    elif verification_result.result == "unresolved":
        status = "unresolved"
        verification_source = "customer_confirmation"
        customer_response = None
        handoff_reason = "The customer confirmed that the proposed steps did not resolve the problem."
    else:
        status = "waiting_for_verification"
        verification_source = None
        customer_response = "目前还不能确认问题已经解决。完成建议步骤后，原来的问题是否仍然存在？"
        handoff_reason = None

    if customer_response is None:
        return {"customer_response": None, "verification_source": verification_source, "handoff_reason": handoff_reason, "status": status}

    messages = state["messages"].copy()
    messages.append(f"Agent: {customer_response}")

    return {"messages": messages[-12:], "customer_response": customer_response, "verification_source": verification_source, "handoff_reason": handoff_reason, "status": status}


def choose_step_after_customer_resolution(state: CustomerSupportState) -> str:
    if state["error"] is not None:
        return "error"

    if state["status"] == "unresolved":
        return "build_support_handoff"

    return "end"


def create_handoff_facts(customer_side_data: dict[str, object]) -> list[SupportFact]:
    facts: list[SupportFact] = []
    sources = {
        "current_product_context": "ResolveLab customer product context",
        "recent_activity": "ResolveLab recent customer activity",
    }

    for data_name, source in sources.items():
        data = customer_side_data.get(data_name)

        if isinstance(data, dict) is False:
            continue

        for name, value in data.items():
            if value is not None:
                facts.append(SupportFact(name=str(name), value=str(value), source=source))

    return facts


def build_support_handoff(state: CustomerSupportState) -> dict[str, object]:
    problem_details = state["problem_details"]
    handoff_reason = state["handoff_reason"]

    if problem_details is None:
        return {"status": "error", "error": "Problem details are missing"}

    if handoff_reason is None:
        return {"status": "error", "error": "Handoff reason is missing"}

    customer_side_data = state["customer_side_data"]

    if len(state["verification_customer_side_data"]) > 0:
        customer_side_data = state["verification_customer_side_data"]

    try:
        handoff_summary = create_support_handoff_summary(problem_details, state["messages"])
        citation_ids = []

        for citation in state["citations"]:
            chunk_id = citation.get("chunk_id")

            if isinstance(chunk_id, str):
                citation_ids.append(chunk_id)

        attempted_steps = []

        if state["resolution"] is not None:
            attempted_steps = state["resolution"].steps.copy()

        handoff = SupportHandoff(
            support_session_id=state["session_id"],
            customer_id=state["customer_id"],
            issue_summary=handoff_summary.issue_summary,
            affected_feature=problem_details.affected_feature,
            customer_impact=handoff_summary.customer_impact,
            approximate_start_time=handoff_summary.approximate_start_time,
            environment_snapshot=customer_side_data.copy(),
            collected_facts=create_handoff_facts(customer_side_data),
            attempted_steps=attempted_steps,
            citation_ids=citation_ids,
            remaining_questions=problem_details.missing_information.copy(),
            handoff_reason=handoff_reason,
        )

        return {"handoff": handoff}
    except Exception as error:
        return {"status": "error", "error": str(error)}


def choose_step_after_handoff(state: CustomerSupportState) -> str:
    if state["error"] is not None or state["handoff"] is None:
        return "error"

    return "create_support_ticket"


def create_support_ticket(state: CustomerSupportState) -> dict[str, object]:
    handoff = state["handoff"]

    if handoff is None:
        return {"status": "error", "error": "Support handoff is missing"}

    try:
        ticket_id = create_ticket_from_handoff(handoff)
        investigation = run_support_investigation(ticket_id)
    except Exception as error:
        return {"status": "error", "error": str(error)}

    customer_response = investigation.result.customer_explanation
    messages = state["messages"].copy()
    messages.append(f"Agent: {customer_response}")

    if investigation.status == TicketStatus.AWAITING_APPROVAL:
        status = "waiting_for_approval"
    elif investigation.result.outcome == "resolution":
        status = "support_resolved"
    elif investigation.result.outcome == "action_required":
        status = "action_required"
    else:
        status = "engineer_escalation"

    return {"messages": messages[-12:], "customer_response": customer_response, "ticket_id": ticket_id, "status": status}


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
customer_support_graph_builder.add_node("build_support_handoff", build_support_handoff)
customer_support_graph_builder.add_node("create_support_ticket", create_support_ticket)

customer_support_graph_builder.add_edge(START, "save_customer_message")
customer_support_graph_builder.add_conditional_edges("save_customer_message", choose_step_after_message, {"update_problem_details": "update_problem_details", "wait_for_customer_verification": "wait_for_customer_verification"})
customer_support_graph_builder.add_edge("update_problem_details", "get_customer_side_data")
customer_support_graph_builder.add_edge("get_customer_side_data", "update_problem_with_customer_side_data")
customer_support_graph_builder.add_conditional_edges("update_problem_with_customer_side_data", choose_next_step, {"ask_for_information": "ask_for_information", "retrieve_customer_documents": "retrieve_customer_documents", "needs_assistance": "needs_assistance", "build_support_handoff": "build_support_handoff", "error": END})
customer_support_graph_builder.add_conditional_edges("retrieve_customer_documents", choose_step_after_document_retrieval, {"prepare_customer_resolution": "prepare_customer_resolution", "needs_assistance": "needs_assistance", "error": END})
customer_support_graph_builder.add_conditional_edges("prepare_customer_resolution", choose_step_after_resolution, {"offer_customer_resolution": "offer_customer_resolution", "needs_assistance": "needs_assistance", "error": END})
customer_support_graph_builder.add_conditional_edges("ask_for_information", choose_step_after_customer_question, {"build_support_handoff": "build_support_handoff", "end": END, "error": END})
customer_support_graph_builder.add_edge("offer_customer_resolution", END)
customer_support_graph_builder.add_edge("needs_assistance", "build_support_handoff")
customer_support_graph_builder.add_conditional_edges("wait_for_customer_verification", choose_step_after_customer_verification, {"verify_resolution_with_tools": "verify_resolution_with_tools", "finalize_customer_resolution": "finalize_customer_resolution", "error": END})
customer_support_graph_builder.add_edge("verify_resolution_with_tools", "finalize_customer_resolution")
customer_support_graph_builder.add_conditional_edges("finalize_customer_resolution", choose_step_after_customer_resolution, {"build_support_handoff": "build_support_handoff", "end": END, "error": END})
customer_support_graph_builder.add_conditional_edges("build_support_handoff", choose_step_after_handoff, {"create_support_ticket": "create_support_ticket", "error": END})
customer_support_graph_builder.add_edge("create_support_ticket", END)

def build_customer_support_graph(checkpointer):
    return customer_support_graph_builder.compile(checkpointer=checkpointer)


def get_customer_support_snapshot(session_id: str):
    thread_id = get_support_session_thread_id(session_id)
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 16}

    with checkpointing.open_postgres_checkpointer() as checkpointer:
        customer_graph = build_customer_support_graph(checkpointer)
        return customer_graph.get_state(config)


def build_support_response(state: CustomerSupportState) -> SupportResponse:
    if state["error"] is not None:
        raise RuntimeError(state["error"])

    if state["problem_details"] is None or state["customer_response"] is None:
        raise RuntimeError("Customer support did not produce a response")

    messages: list[CustomerConversationMessage] = []

    for saved_message in state["messages"]:
        if saved_message.startswith("Customer: "):
            messages.append(CustomerConversationMessage(role="customer", content=saved_message.removeprefix("Customer: ")))
        elif saved_message.startswith("Agent: "):
            messages.append(CustomerConversationMessage(role="assistant", content=saved_message.removeprefix("Agent: ")))

    response_status = state["status"]
    customer_response = state["customer_response"]
    saved_status, saved_customer_result = get_support_session_result(state["session_id"])

    if saved_status in ("waiting_for_approval", "support_resolved", "action_required", "engineer_escalation") and saved_customer_result is not None:
        response_status = saved_status
        customer_response = saved_customer_result

        if len(messages) == 0 or messages[-1].role != "assistant" or messages[-1].content != saved_customer_result:
            messages.append(CustomerConversationMessage(role="assistant", content=saved_customer_result))

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
            account_status_text = {"active": "正常", "inactive": "停用", "suspended": "暂停"}.get(account_status.strip().lower(), "暂时无法确认")
            customer_facts.append(f"账户状态：{account_status_text}。")

        if isinstance(product_version, str):
            customer_facts.append(f"产品版本：{product_version}。")

        if isinstance(affected_feature, str) and isinstance(feature_enabled, bool):
            feature_status = "已关闭"

            if feature_enabled:
                feature_status = "已开启"

            affected_feature_text = {"order notifications": "订单通知", "report exports": "报表导出"}.get(affected_feature.strip().lower(), "当前功能")
            customer_facts.append(f"{affected_feature_text}：{feature_status}。")

    recent_activity = customer_side_data.get("recent_activity")

    if isinstance(recent_activity, dict):
        activity = recent_activity.get("activity")
        result = recent_activity.get("result")
        occurred_at = recent_activity.get("occurred_at")

        if isinstance(activity, str) and isinstance(result, str) and isinstance(occurred_at, str):
            result_text = {"failed": "失败", "disabled": "功能未开启", "delivered": "已送达", "success": "成功", "successful": "成功", "succeeded": "成功", "completed": "已完成"}.get(result.strip().lower(), "暂时无法确认")
            activity_text = {"sending the latest order notification": "发送最近一条订单通知", "sending an order notification": "发送订单通知", "exporting the latest report": "导出最近一份报表"}.get(activity.strip().lower(), "最近一次操作")
            customer_facts.append(f"最近活动：{activity_text}。结果：{result_text}。记录时间：{occurred_at}。")

    ticket_id = get_ticket_id_for_support_session(state["session_id"])
    return SupportResponse(session_id=state["session_id"], messages=messages, problem_details=state["problem_details"], customer_response=customer_response, customer_facts=customer_facts, citations=state["citations"], resolution=state["resolution"], verification_result=state["verification_result"], verification_source=state["verification_source"], ticket_id=ticket_id, status=response_status)


def persist_customer_support_state(state: CustomerSupportState) -> None:
    problem_details = None

    if state["problem_details"] is not None:
        problem_details = state["problem_details"].model_dump(mode="json")

    save_support_session_progress(state["session_id"], state["status"], problem_details, state["customer_response"])


def start_customer_support(customer_id: str, customer_message: str) -> SupportResponse:
    session_id = str(uuid4())
    create_support_session_record(session_id, customer_id, session_id)

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
        "handoff": None,
        "handoff_reason": None,
        "ticket_id": None,
        "turn_count": 0,
        "customer_response": None,
        "status": "started",
        "error": None,
    }

    thread_id = get_support_session_thread_id(session_id)
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 16}

    with checkpointing.open_postgres_checkpointer() as checkpointer:
        customer_graph = build_customer_support_graph(checkpointer)
        final_state = customer_graph.invoke(initial_state, config)

    persist_customer_support_state(final_state)

    return build_support_response(final_state)


def read_customer_support(session_id: str) -> SupportResponse:
    saved_state = get_customer_support_snapshot(session_id)

    if len(saved_state.values) == 0:
        raise RuntimeError("Support session state not found")

    return build_support_response(saved_state.values)


def continue_customer_support(session_id: str, customer_message: str) -> SupportResponse:
    thread_id = get_support_session_thread_id(session_id)
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 16}

    with checkpointing.open_postgres_checkpointer() as checkpointer:
        customer_graph = build_customer_support_graph(checkpointer)
        saved_state = customer_graph.get_state(config)

        if len(saved_state.values) == 0:
            raise RuntimeError("Support session state not found")

        if len(saved_state.next) > 0:
            final_state = customer_graph.invoke(None, config)
        elif saved_state.values["status"] in ("resolved", "unresolved", "needs_assistance", "support_resolved", "action_required", "waiting_for_approval", "engineer_escalation"):
            return build_support_response(saved_state.values)
        else:
            final_state = customer_graph.invoke({"customer_message": customer_message, "error": None}, config)

    persist_customer_support_state(final_state)

    return build_support_response(final_state)
