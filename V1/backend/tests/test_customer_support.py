import os
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import actions, customer_agent, customer_workflow, main, support_sessions, support_workflow, tickets
from app.customer_agent import CustomerResolution, CustomerVerification, ProblemDetails
from app.db.database import Base
from app.db.models import SupportSession, Ticket
from app.handoff import SupportHandoffSummary
from app.support_agent import SupportInvestigationRun
from app.support_evidence import create_internal_knowledge_evidence, create_tool_evidence
from app.support_results import SupportInvestigationResult
from app.support_workflow import SupportInvestigationResponse
from app.tickets import TicketContext

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def set_default_customer_side_data(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

    current_product_context = {
        "account_status": "active",
        "product_version": "2026.8",
        "affected_feature": "order notifications",
        "feature_enabled": True,
    }

    def fake_get_current_product_context(customer_id: str) -> dict[str, object]:
        return current_product_context

    def fake_should_get_recent_customer_activity(problem_details: ProblemDetails, received_product_context: dict[str, object]) -> bool:
        return False

    def fake_update_customer_problem_with_customer_side_data(problem_details: ProblemDetails, customer_side_data: dict[str, object]) -> ProblemDetails:
        return problem_details

    def fake_create_support_handoff_summary(problem_details: ProblemDetails, customer_messages: list[str]) -> SupportHandoffSummary:
        return SupportHandoffSummary(issue_summary="The customer cannot complete the current operation.", customer_impact="The customer cannot complete the current operation.", approximate_start_time=None)

    def fake_run_support_investigation(ticket_id: int) -> SupportInvestigationResponse:
        result = SupportInvestigationResult(
            conclusion="The available information is insufficient and requires engineer investigation.",
            supporting_facts=[],
            customer_explanation="我们暂时无法确认问题原因，已经交给工程师继续检查。你不需要重复说明已经提供的信息。",
            outcome="engineer_escalation",
        )
        support_workflow.save_support_result(ticket_id, result, [])
        return SupportInvestigationResponse(ticket_id=ticket_id, result=result, tools_used=[])

    monkeypatch.setattr(actions, "SessionLocal", test_session)
    monkeypatch.setattr(support_sessions, "SessionLocal", test_session)
    monkeypatch.setattr(support_workflow, "SessionLocal", test_session)
    monkeypatch.setattr(tickets, "SessionLocal", test_session)
    monkeypatch.setattr(customer_workflow, "get_current_product_context", fake_get_current_product_context)
    monkeypatch.setattr(customer_workflow, "should_get_recent_customer_activity", fake_should_get_recent_customer_activity)
    monkeypatch.setattr(customer_workflow, "update_customer_problem_with_customer_side_data", fake_update_customer_problem_with_customer_side_data)
    monkeypatch.setattr(customer_workflow, "create_support_handoff_summary", fake_create_support_handoff_summary)
    monkeypatch.setattr(customer_workflow, "run_support_investigation", fake_run_support_investigation)

    yield

    Base.metadata.drop_all(engine)
    engine.dispose()


def configure_resolvable_customer_path(monkeypatch: pytest.MonkeyPatch) -> tuple[ProblemDetails, CustomerResolution]:
    problem_details = ProblemDetails(
        summary="订单通知从今天开始无法送达。",
        affected_feature="order notifications",
        problem="Order notifications are not arriving.",
        customer_goal="恢复接收订单通知。",
        missing_information=[],
    )
    resolution = CustomerResolution(
        can_resolve=True,
        explanation="已保存的通知地址可能需要重新保存。",
        steps=["打开通知设置并重新保存接收地址。", "发送一条测试订单通知。"],
        citation_ids=["docs/customer/order-notifications.md:0"],
        verification_method="customer_confirmation_or_tool",
    )

    def fake_update_customer_problem(customer_messages: list[str], current_problem_details: ProblemDetails | None) -> ProblemDetails:
        return problem_details

    class FakeCustomerDocumentSearch:
        def invoke(self, search_input: dict[str, object]) -> list[dict[str, object]]:
            assert search_input["version"] == "2026.8"
            return [{"chunk_id": "docs/customer/order-notifications.md:0", "source_uri": "docs/customer/order-notifications.md", "version": "2026.8", "content": "重新保存通知地址，然后发送一条测试通知。"}]

    def fake_create_customer_resolution_from_documents(received_problem_details: ProblemDetails, customer_side_data: dict[str, object], retrieved_customer_documents: list[dict[str, object]]) -> CustomerResolution:
        assert received_problem_details == problem_details
        assert customer_side_data["current_product_context"]["product_version"] == "2026.8"
        assert retrieved_customer_documents[0]["chunk_id"] == "docs/customer/order-notifications.md:0"
        return resolution

    monkeypatch.setattr(customer_workflow, "update_customer_problem", fake_update_customer_problem)
    monkeypatch.setattr(customer_workflow, "retrieve_documents_for_customer_question", FakeCustomerDocumentSearch())
    monkeypatch.setattr(customer_workflow, "create_customer_resolution_from_documents", fake_create_customer_resolution_from_documents)

    return problem_details, resolution


def test_start_support_session_asks_one_question(monkeypatch: pytest.MonkeyPatch) -> None:
    problem_details = ProblemDetails(
        summary="The customer says a feature has not worked today.",
        affected_feature="unknown",
        problem="The feature does not work.",
        customer_goal="Use the feature normally.",
        missing_information=["feature name"],
    )

    def fake_update_customer_problem(customer_messages: list[str], current_problem_details: ProblemDetails | None) -> ProblemDetails:
        assert customer_messages == ["Customer: This feature has not worked all day."]
        assert current_problem_details is None
        return problem_details

    def fake_create_customer_question(received_problem_details: ProblemDetails, asked_questions: list[str]) -> str:
        assert received_problem_details == problem_details
        assert asked_questions == []
        return "Which feature is not working? This will help me understand where the problem happens."

    monkeypatch.setattr(customer_workflow, "update_customer_problem", fake_update_customer_problem)
    monkeypatch.setattr(customer_workflow, "create_customer_question", fake_create_customer_question)

    response = client.post("/api/v1/support-sessions", json={"customer_id": "customer_001", "message": "This feature has not worked all day."})
    response_data = response.json()
    session_id = response_data["session_id"]
    saved_state = customer_workflow.get_customer_support_snapshot(session_id)

    assert response.status_code == 201
    assert response_data["problem_details"] == problem_details.model_dump(mode="json")
    assert response_data["customer_response"] == "Which feature is not working? This will help me understand where the problem happens."
    assert response_data["status"] == "waiting_for_customer"
    assert saved_state.values["customer_id"] == "customer_001"
    assert saved_state.values["turn_count"] == 1
    assert saved_state.values["asked_questions"] == [response_data["customer_response"]]


def test_continue_support_session_updates_the_same_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    first_problem_details = ProblemDetails(
        summary="The customer cannot use a feature.",
        affected_feature="unknown",
        problem="A feature does not work.",
        customer_goal="Use the feature.",
        missing_information=["feature name"],
    )
    updated_problem_details = ProblemDetails(
        summary="The customer cannot use invoice export.",
        affected_feature="invoice export",
        problem="Invoice export does not start.",
        customer_goal="Export an invoice.",
        missing_information=[],
    )

    def fake_update_customer_problem(customer_messages: list[str], current_problem_details: ProblemDetails | None) -> ProblemDetails:
        if current_problem_details is None:
            return first_problem_details

        assert current_problem_details == first_problem_details
        assert customer_messages[-1] == "Customer: It is the invoice export feature."
        return updated_problem_details

    def fake_create_customer_question(problem_details: ProblemDetails, asked_questions: list[str]) -> str:
        return "Which feature is not working? This will help me understand where the problem happens."

    class FakeCustomerDocumentSearch:
        def invoke(self, search_input: dict[str, object]) -> list[dict[str, object]]:
            assert search_input == {"customer_question": "The customer cannot use invoice export.\nAffected feature: invoice export\nInvoice export does not start.\nCustomer goal: Export an invoice.", "version": "2026.8"}
            return [{"chunk_id": "docs/customer/recovery.md:0", "source_uri": "docs/customer/recovery.md", "version": "2026.8", "content": "Save the destination again, then send one test notification."}]

    def fake_create_customer_resolution_from_documents(problem_details: ProblemDetails, customer_side_data: dict[str, object], retrieved_customer_documents: list[dict[str, object]]) -> CustomerResolution:
        return CustomerResolution(can_resolve=True, explanation="The saved destination may need to be refreshed.", steps=["Save the destination again.", "Send one test notification."], citation_ids=["docs/customer/recovery.md:0"], verification_method="customer_confirmation_or_tool")

    monkeypatch.setattr(customer_workflow, "update_customer_problem", fake_update_customer_problem)
    monkeypatch.setattr(customer_workflow, "create_customer_question", fake_create_customer_question)
    monkeypatch.setattr(customer_workflow, "retrieve_documents_for_customer_question", FakeCustomerDocumentSearch())
    monkeypatch.setattr(customer_workflow, "create_customer_resolution_from_documents", fake_create_customer_resolution_from_documents)

    first_response = client.post("/api/v1/support-sessions", json={"customer_id": "customer_001", "message": "A feature does not work."})
    session_id = first_response.json()["session_id"]
    second_response = client.post(f"/api/v1/support-sessions/{session_id}/messages", json={"message": "It is the invoice export feature."})
    second_response_data = second_response.json()
    saved_state = customer_workflow.get_customer_support_snapshot(session_id)

    assert first_response.status_code == 201
    assert second_response.status_code == 200
    assert second_response_data["session_id"] == session_id
    assert second_response_data["problem_details"] == updated_problem_details.model_dump(mode="json")
    assert second_response_data["customer_response"] == "The saved destination may need to be refreshed.\n\n1. Save the destination again.\n2. Send one test notification.\n\n完成以上步骤后，请告诉我问题是否已经解决。"
    assert second_response_data["citations"] == [{"chunk_id": "docs/customer/recovery.md:0", "source_uri": "docs/customer/recovery.md", "version": "2026.8"}]
    assert second_response_data["resolution"]["verification_method"] == "customer_confirmation_or_tool"
    assert second_response_data["status"] == "waiting_for_verification"
    assert saved_state.values["turn_count"] == 2


def test_support_session_stops_after_three_questions(monkeypatch: pytest.MonkeyPatch) -> None:
    problem_details = ProblemDetails(
        summary="The customer has not provided enough information.",
        affected_feature="unknown",
        problem="Something does not work.",
        customer_goal="Use the product.",
        missing_information=["affected feature", "what happens", "when it started"],
    )
    questions = [
        "Which feature is affected? This will help me locate the problem.",
        "What happens when you try it? This will help me understand the failure.",
        "When did this start? This will help me understand the timing.",
    ]
    question_number = 0

    def fake_update_customer_problem(customer_messages: list[str], current_problem_details: ProblemDetails | None) -> ProblemDetails:
        return problem_details

    def fake_create_customer_question(received_problem_details: ProblemDetails, asked_questions: list[str]) -> str:
        nonlocal question_number
        question = questions[question_number]
        question_number += 1
        return question

    monkeypatch.setattr(customer_workflow, "update_customer_problem", fake_update_customer_problem)
    monkeypatch.setattr(customer_workflow, "create_customer_question", fake_create_customer_question)

    first_response = client.post("/api/v1/support-sessions", json={"customer_id": "customer_001", "message": "It does not work."})
    session_id = first_response.json()["session_id"]
    client.post(f"/api/v1/support-sessions/{session_id}/messages", json={"message": "I am not sure."})
    client.post(f"/api/v1/support-sessions/{session_id}/messages", json={"message": "I still do not know."})
    final_response = client.post(f"/api/v1/support-sessions/{session_id}/messages", json={"message": "I cannot provide more details."})
    saved_state = customer_workflow.get_customer_support_snapshot(session_id)

    assert final_response.status_code == 200
    assert final_response.json()["status"] == "engineer_escalation"
    assert question_number == 3
    assert saved_state.values["asked_questions"] == questions
    assert saved_state.values["turn_count"] == 4


def test_support_session_does_not_repeat_a_question(monkeypatch: pytest.MonkeyPatch) -> None:
    problem_details = ProblemDetails(
        summary="The customer has not provided enough information.",
        affected_feature="unknown",
        problem="Something does not work.",
        customer_goal="Use the product.",
        missing_information=["affected feature"],
    )
    repeated_question = "Which feature is affected? This will help me locate the problem."

    def fake_update_customer_problem(customer_messages: list[str], current_problem_details: ProblemDetails | None) -> ProblemDetails:
        return problem_details

    def fake_create_customer_question(received_problem_details: ProblemDetails, asked_questions: list[str]) -> str:
        return repeated_question

    monkeypatch.setattr(customer_workflow, "update_customer_problem", fake_update_customer_problem)
    monkeypatch.setattr(customer_workflow, "create_customer_question", fake_create_customer_question)

    first_response = client.post("/api/v1/support-sessions", json={"customer_id": "customer_001", "message": "It does not work."})
    session_id = first_response.json()["session_id"]
    second_response = client.post(f"/api/v1/support-sessions/{session_id}/messages", json={"message": "I do not know."})
    saved_state = customer_workflow.get_customer_support_snapshot(session_id)

    assert second_response.status_code == 200
    assert second_response.json()["status"] == "engineer_escalation"
    assert saved_state.values["asked_questions"] == [repeated_question]


def test_continue_support_session_returns_not_found() -> None:
    response = client.post("/api/v1/support-sessions/missing-session/messages", json={"message": "More information"})

    assert response.status_code == 404
    assert response.json() == {"detail": "Support session not found"}


def test_start_support_session_returns_error_when_agent_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_update_customer_problem(customer_messages: list[str], current_problem_details: ProblemDetails | None) -> ProblemDetails:
        raise RuntimeError("Model request failed")

    monkeypatch.setattr(customer_workflow, "update_customer_problem", fake_update_customer_problem)

    response = client.post("/api/v1/support-sessions", json={"customer_id": "customer_001", "message": "It does not work."})

    assert response.status_code == 502
    assert response.json() == {"detail": "Customer support failed"}


def test_customer_side_data_uses_customer_id_from_session(monkeypatch: pytest.MonkeyPatch) -> None:
    problem_details = ProblemDetails(
        summary="Order notifications are not being received.",
        affected_feature="order notifications",
        problem="Order notifications are not arriving.",
        customer_goal="Receive order notifications.",
        missing_information=["recent activity"],
    )
    current_product_context = {
        "account_status": "active",
        "product_version": "2026.8",
        "affected_feature": "order notifications",
        "feature_enabled": True,
    }
    requested_customer_ids = []

    def fake_get_current_product_context(customer_id: str) -> dict[str, object]:
        requested_customer_ids.append(customer_id)
        return current_product_context

    def fake_should_get_recent_customer_activity(received_problem_details: ProblemDetails, received_product_context: dict[str, object]) -> bool:
        assert received_problem_details == problem_details
        assert received_product_context == current_product_context
        return True

    def fake_get_recent_customer_activity(customer_id: str) -> dict[str, object]:
        requested_customer_ids.append(customer_id)
        return {"affected_feature": "order notifications", "activity": "Sending the latest order notification", "result": "failed", "occurred_at": "2026-08-25T09:20:00Z"}

    monkeypatch.setattr(customer_workflow, "get_current_product_context", fake_get_current_product_context)
    monkeypatch.setattr(customer_workflow, "should_get_recent_customer_activity", fake_should_get_recent_customer_activity)
    monkeypatch.setattr(customer_workflow, "get_recent_customer_activity", fake_get_recent_customer_activity)

    state: customer_workflow.CustomerSupportState = {
        "session_id": "session_001",
        "customer_id": "customer_001",
        "customer_message": "My order notifications are not arriving.",
        "messages": ["Customer: My order notifications are not arriving."],
        "problem_details": problem_details,
        "customer_side_data": {},
        "verification_customer_side_data": {},
        "asked_questions": [],
        "missing_information": problem_details.missing_information,
        "retrieved_customer_documents": [],
        "citations": [],
        "resolution": None,
        "verification_result": None,
        "verification_source": None,
        "handoff": None,
        "handoff_reason": None,
        "ticket_id": None,
        "turn_count": 1,
        "customer_response": None,
        "status": "started",
        "error": None,
    }

    result = customer_workflow.get_customer_side_data(state)

    assert requested_customer_ids == ["customer_001", "customer_001"]
    assert result["customer_side_data"] == {
        "current_product_context": current_product_context,
        "recent_activity": {"affected_feature": "order notifications", "activity": "Sending the latest order notification", "result": "failed", "occurred_at": "2026-08-25T09:20:00Z"},
    }


def test_support_session_does_not_ask_for_known_product_version(monkeypatch: pytest.MonkeyPatch) -> None:
    problem_before_customer_side_data = ProblemDetails(
        summary="Order notifications are not being received.",
        affected_feature="order notifications",
        problem="Order notifications are not arriving.",
        customer_goal="Receive order notifications.",
        missing_information=["product version", "when the problem started"],
    )
    problem_after_customer_side_data = ProblemDetails(
        summary="Order notifications are not being received on product version 2026.8.",
        affected_feature="order notifications",
        problem="Order notifications are not arriving.",
        customer_goal="Receive order notifications.",
        missing_information=["when the problem started"],
    )

    def fake_update_customer_problem(customer_messages: list[str], current_problem_details: ProblemDetails | None) -> ProblemDetails:
        return problem_before_customer_side_data

    def fake_update_customer_problem_with_customer_side_data(problem_details: ProblemDetails, customer_side_data: dict[str, object]) -> ProblemDetails:
        assert customer_side_data["current_product_context"]["product_version"] == "2026.8"
        return problem_after_customer_side_data

    def fake_create_customer_question(problem_details: ProblemDetails, asked_questions: list[str]) -> str:
        assert problem_details.missing_information == ["when the problem started"]
        return "When did this problem start? This will help me understand what may have changed."

    monkeypatch.setattr(customer_workflow, "update_customer_problem", fake_update_customer_problem)
    monkeypatch.setattr(customer_workflow, "update_customer_problem_with_customer_side_data", fake_update_customer_problem_with_customer_side_data)
    monkeypatch.setattr(customer_workflow, "create_customer_question", fake_create_customer_question)

    response = client.post("/api/v1/support-sessions", json={"customer_id": "customer_001", "message": "My order notifications are not arriving."})
    response_data = response.json()
    session_id = response_data["session_id"]
    saved_state = customer_workflow.get_customer_support_snapshot(session_id)

    assert response.status_code == 201
    assert response_data["customer_response"] == "When did this problem start? This will help me understand what may have changed."
    assert "version" not in response_data["customer_response"].lower()
    assert saved_state.values["customer_side_data"]["current_product_context"]["product_version"] == "2026.8"


def test_customer_completes_the_day_five_self_service_path(monkeypatch: pytest.MonkeyPatch) -> None:
    problem_details, resolution = configure_resolvable_customer_path(monkeypatch)
    customer_confirmation = "通知已经恢复，问题解决了。"

    def fake_verify_customer_resolution(received_problem_details: ProblemDetails, received_resolution: CustomerResolution, customer_message: str) -> CustomerVerification:
        assert received_problem_details == problem_details
        assert received_resolution == resolution
        assert customer_message == customer_confirmation
        return CustomerVerification(result="resolved", supporting_text="问题解决了")

    monkeypatch.setattr(customer_workflow, "verify_customer_resolution", fake_verify_customer_resolution)

    first_response = client.post("/api/v1/support-sessions", json={"customer_id": "customer_001", "message": "我的订单通知从今天开始收不到了，我希望恢复接收。"})
    first_response_data = first_response.json()

    assert first_response.status_code == 201
    assert first_response_data["status"] == "waiting_for_verification"
    assert first_response_data["resolution"] == resolution.model_dump(mode="json")
    assert first_response_data["citations"] == [{"chunk_id": "docs/customer/order-notifications.md:0", "source_uri": "docs/customer/order-notifications.md", "version": "2026.8"}]
    assert first_response_data["customer_facts"] == ["账户状态：正常。", "产品版本：2026.8。", "订单通知：已开启。"]
    assert "1. 打开通知设置并重新保存接收地址。" in first_response_data["customer_response"]
    assert "2. 发送一条测试订单通知。" in first_response_data["customer_response"]

    session_id = first_response_data["session_id"]
    final_response = client.post(f"/api/v1/support-sessions/{session_id}/messages", json={"message": customer_confirmation})
    final_response_data = final_response.json()
    saved_state = customer_workflow.get_customer_support_snapshot(session_id)

    assert final_response.status_code == 200
    assert final_response_data["status"] == "resolved"
    assert final_response_data["verification_source"] == "customer_confirmation"
    assert final_response_data["verification_result"] == {"result": "resolved", "supporting_text": "问题解决了"}
    assert saved_state.values["turn_count"] == 2

    with support_sessions.SessionLocal() as database:
        saved_session = database.get(SupportSession, session_id)
        ticket_count = database.scalar(select(func.count()).select_from(Ticket))

    assert saved_session is not None
    assert saved_session.status == "resolved"
    assert saved_session.final_problem_details == problem_details.model_dump(mode="json")
    assert ticket_count == 0


def test_customer_can_report_that_the_resolution_did_not_work(monkeypatch: pytest.MonkeyPatch) -> None:
    problem_details, resolution = configure_resolvable_customer_path(monkeypatch)
    customer_feedback = "我完成了两个步骤，但仍然收不到通知。"

    def fake_verify_customer_resolution(problem_details: ProblemDetails, resolution: CustomerResolution, customer_message: str) -> CustomerVerification:
        return CustomerVerification(result="unresolved", supporting_text="仍然收不到通知")

    monkeypatch.setattr(customer_workflow, "verify_customer_resolution", fake_verify_customer_resolution)

    first_response = client.post("/api/v1/support-sessions", json={"customer_id": "customer_001", "message": "我的订单通知收不到了。"})
    session_id = first_response.json()["session_id"]
    final_response = client.post(f"/api/v1/support-sessions/{session_id}/messages", json={"message": customer_feedback})
    response_data = final_response.json()
    ticket_id = response_data["ticket_id"]
    ticket_response = client.get(f"/api/v1/tickets/{ticket_id}")

    assert final_response.status_code == 200
    assert response_data["status"] == "engineer_escalation"
    assert response_data["verification_source"] == "customer_confirmation"
    assert response_data["verification_result"] == {"result": "unresolved", "supporting_text": "仍然收不到通知"}
    assert isinstance(ticket_id, int)
    assert response_data["customer_response"] == "我们暂时无法确认问题原因，已经交给工程师继续检查。你不需要重复说明已经提供的信息。"
    assert ticket_response.status_code == 200
    assert ticket_response.json()["handoff"]["support_session_id"] == session_id
    assert ticket_response.json()["handoff"]["customer_id"] == "customer_001"
    assert ticket_response.json()["handoff"]["issue_summary"] == "The customer cannot complete the current operation."
    assert ticket_response.json()["handoff"]["attempted_steps"] == resolution.steps
    assert ticket_response.json()["handoff"]["citation_ids"] == resolution.citation_ids
    assert ticket_response.json()["handoff"]["handoff_reason"] == "The customer confirmed that the proposed steps did not resolve the problem."

    with support_sessions.SessionLocal() as database:
        saved_session = database.get(SupportSession, session_id)
        saved_ticket = database.get(Ticket, ticket_id)

    assert saved_session is not None
    assert saved_session.status == "engineer_escalation"
    assert saved_session.final_problem_details == problem_details.model_dump(mode="json")
    assert saved_ticket is not None
    assert saved_ticket.handoff == ticket_response.json()["handoff"]


def test_day_seven_customer_ticket_support_graph_and_safe_result(monkeypatch: pytest.MonkeyPatch) -> None:
    problem_details = ProblemDetails(
        summary="订单通知从今天开始无法送达。",
        affected_feature="order notifications",
        problem="Order notifications are not arriving.",
        customer_goal="恢复接收订单通知。",
        missing_information=[],
    )
    expected_result = SupportInvestigationResult(
        conclusion="The platform is operational, and the customer endpoint rejected the two latest notifications with HTTP 401.",
        supporting_facts=["The platform status is operational.", "The two latest notifications returned HTTP 401."],
        customer_explanation="我们确认通知已经发出，但你的接收地址拒绝了请求。请检查接收端的访问设置后再试。",
        outcome="resolution",
        root_cause="The receiving endpoint rejected the notifications.",
        confidence_band="high",
        resolution="Check the receiving endpoint access settings.",
    )

    def fake_update_customer_problem(customer_messages: list[str], current_problem_details: ProblemDetails | None) -> ProblemDetails:
        return problem_details

    def fake_create_support_handoff_summary(problem_details: ProblemDetails, customer_messages: list[str]) -> SupportHandoffSummary:
        return SupportHandoffSummary(
            issue_summary="Order notifications have not been delivered since today.",
            customer_impact="The customer cannot receive order status updates.",
            approximate_start_time="Today",
        )

    class EmptyCustomerDocumentSearch:
        def invoke(self, search_input: dict[str, object]) -> list[dict[str, object]]:
            return []

    def fake_investigate_support_ticket(ticket_context: TicketContext) -> SupportInvestigationRun:
        assert ticket_context.handoff is not None
        assert ticket_context.handoff.issue_summary == "Order notifications have not been delivered since today."
        assert ticket_context.handoff.customer_id == "customer_001"
        observed_at = datetime.now(timezone.utc).isoformat()
        evidence = create_tool_evidence(ticket_context.id, "customer_001", "get_event_notification_deliveries", [{"customer_id": "customer_001", "delivery_id": "delivery_001", "delivery_status": "failed", "response_status": 401, "attempted_at": observed_at}])
        evidence.extend(create_tool_evidence(ticket_context.id, "customer_001", "get_platform_status", {"service": "event_notifications", "status": "operational", "updated_at": observed_at}))
        document = {"chunk_id": "docs/internal/event-notification-401.md:0", "source_uri": "docs/internal/event-notification-401.md", "version": "2026.8", "content": "HTTP 401 means the receiving endpoint rejected authentication.", "score": 0.9, "visibility": "INTERNAL", "feature": "order notifications", "effective_from": "2026-08-01", "effective_to": None}
        evidence.extend(create_internal_knowledge_evidence(ticket_context.id, "customer_001", "2026.8", "order notifications", [document]))
        expected_result.evidence = evidence
        expected_result.supporting_evidence_ids = [item.evidence_id for item in evidence]
        expected_result.supporting_facts = [item.summary for item in evidence]
        expected_result.internal_citation_ids = ["docs/internal/event-notification-401.md:0"]
        return SupportInvestigationRun(
            result=expected_result,
            tools_used=["get_event_notification_deliveries", "get_platform_status", "search_internal_knowledge"],
            evidence=evidence,
        )

    monkeypatch.setattr(customer_workflow, "update_customer_problem", fake_update_customer_problem)
    monkeypatch.setattr(customer_workflow, "create_support_handoff_summary", fake_create_support_handoff_summary)
    monkeypatch.setattr(customer_workflow, "retrieve_documents_for_customer_question", EmptyCustomerDocumentSearch())
    monkeypatch.setattr(support_workflow, "investigate_support_ticket", fake_investigate_support_ticket)
    monkeypatch.setattr(customer_workflow, "run_support_investigation", support_workflow.run_support_investigation)

    response = client.post(
        "/api/v1/support-sessions",
        json={"customer_id": "customer_001", "message": "我的订单通知从今天开始收不到了，我希望恢复接收。"},
    )
    response_data = response.json()
    ticket_id = response_data["ticket_id"]
    ticket_response = client.get(f"/api/v1/tickets/{ticket_id}")
    ticket_data = ticket_response.json()

    assert response.status_code == 201
    assert response_data["status"] == "support_resolved"
    assert response_data["customer_response"] == expected_result.customer_explanation
    assert "investigation_result" not in response_data
    assert "tools_used" not in response_data
    assert isinstance(ticket_id, int)
    assert ticket_response.status_code == 200
    assert ticket_data["status"] == "RESOLVED"
    assert ticket_data["handoff"]["issue_summary"] == "Order notifications have not been delivered since today."
    assert ticket_data["investigation_result"] == expected_result.model_dump(mode="json")
    assert ticket_data["investigation_tools"] == ["get_event_notification_deliveries", "get_platform_status", "search_internal_knowledge"]

    with support_sessions.SessionLocal() as database:
        saved_session = database.get(SupportSession, response_data["session_id"])

    assert saved_session is not None
    assert saved_session.status == "support_resolved"
    assert saved_session.customer_result == expected_result.customer_explanation


def test_tool_state_change_can_confirm_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_resolvable_customer_path(monkeypatch)
    activity_call_count = 0

    def fake_should_get_recent_customer_activity(problem_details: ProblemDetails, current_product_context: dict[str, object]) -> bool:
        return True

    def fake_get_recent_customer_activity(customer_id: str) -> dict[str, object]:
        nonlocal activity_call_count
        activity_call_count += 1

        if activity_call_count == 1:
            return {"affected_feature": "order notifications", "activity": "Sending an order notification", "result": "failed", "occurred_at": "2026-09-10T09:20:00Z"}

        return {"affected_feature": "order notifications", "activity": "Sending an order notification", "result": "delivered", "occurred_at": "2026-09-10T09:25:00Z"}

    def fake_verify_customer_resolution(problem_details: ProblemDetails, resolution: CustomerResolution, customer_message: str) -> CustomerVerification:
        return CustomerVerification(result="unclear", supporting_text="")

    monkeypatch.setattr(customer_workflow, "should_get_recent_customer_activity", fake_should_get_recent_customer_activity)
    monkeypatch.setattr(customer_workflow, "get_recent_customer_activity", fake_get_recent_customer_activity)
    monkeypatch.setattr(customer_workflow, "verify_customer_resolution", fake_verify_customer_resolution)

    first_response = client.post("/api/v1/support-sessions", json={"customer_id": "customer_001", "message": "My order notifications stopped arriving."})
    session_id = first_response.json()["session_id"]
    final_response = client.post(f"/api/v1/support-sessions/{session_id}/messages", json={"message": "I completed the steps."})
    response_data = final_response.json()

    assert final_response.status_code == 200
    assert activity_call_count == 2
    assert response_data["status"] == "resolved"
    assert response_data["verification_source"] == "tool_verification"
    assert response_data["verification_result"] == {"result": "resolved", "supporting_text": ""}
    assert "结果：已送达。" in response_data["customer_facts"][-1]


def test_unchanged_tool_state_does_not_mark_the_problem_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_resolvable_customer_path(monkeypatch)

    def fake_should_get_recent_customer_activity(problem_details: ProblemDetails, current_product_context: dict[str, object]) -> bool:
        return True

    def fake_get_recent_customer_activity(customer_id: str) -> dict[str, object]:
        return {"affected_feature": "order notifications", "activity": "Sending an order notification", "result": "failed", "occurred_at": "2026-09-10T09:20:00Z"}

    def fake_verify_customer_resolution(problem_details: ProblemDetails, resolution: CustomerResolution, customer_message: str) -> CustomerVerification:
        return CustomerVerification(result="unclear", supporting_text="")

    monkeypatch.setattr(customer_workflow, "should_get_recent_customer_activity", fake_should_get_recent_customer_activity)
    monkeypatch.setattr(customer_workflow, "get_recent_customer_activity", fake_get_recent_customer_activity)
    monkeypatch.setattr(customer_workflow, "verify_customer_resolution", fake_verify_customer_resolution)

    first_response = client.post("/api/v1/support-sessions", json={"customer_id": "customer_001", "message": "My order notifications stopped arriving."})
    session_id = first_response.json()["session_id"]
    follow_up_response = client.post(f"/api/v1/support-sessions/{session_id}/messages", json={"message": "I completed the steps."})
    response_data = follow_up_response.json()

    assert follow_up_response.status_code == 200
    assert response_data["status"] == "waiting_for_verification"
    assert response_data["verification_source"] is None
    assert response_data["verification_result"] == {"result": "unclear", "supporting_text": ""}
    assert "原来的问题是否仍然存在？" in response_data["customer_response"]


def test_customer_verification_requires_an_exact_quote(monkeypatch: pytest.MonkeyPatch) -> None:
    problem_details = ProblemDetails(summary="Order notifications stopped arriving.", affected_feature="order notifications", problem="Notifications are not arriving.", customer_goal="Receive notifications again.", missing_information=[])
    resolution = CustomerResolution(can_resolve=True, explanation="The destination may need to be refreshed.", steps=["Save the destination again."], citation_ids=["docs/customer/order-notifications.md:0"], verification_method="customer_confirmation_or_tool")

    class FakeCustomerVerificationModel:
        def invoke(self, messages: list[object]) -> CustomerVerification:
            return CustomerVerification(result="resolved", supporting_text="The problem is fixed.")

    monkeypatch.setattr(customer_agent, "customer_verification_model", FakeCustomerVerificationModel())

    verification = customer_agent.verify_customer_resolution(problem_details, resolution, "I completed the steps.")

    assert verification == CustomerVerification(result="unclear", supporting_text="")


@pytest.mark.parametrize("invalid_resolution", [CustomerResolution(can_resolve=True, explanation="Try these steps.", steps=["Save the destination again."], citation_ids=["missing-chunk"], verification_method="customer_confirmation_or_tool"), CustomerResolution(can_resolve=True, explanation="", steps=["Save the destination again."], citation_ids=["docs/customer/order-notifications.md:0"], verification_method="customer_confirmation_or_tool")], ids=["unknown-citation", "empty-explanation"])
def test_invalid_resolution_is_not_offered(monkeypatch: pytest.MonkeyPatch, invalid_resolution: CustomerResolution) -> None:
    configure_resolvable_customer_path(monkeypatch)

    def fake_create_customer_resolution_from_documents(problem_details: ProblemDetails, customer_side_data: dict[str, object], retrieved_customer_documents: list[dict[str, object]]) -> CustomerResolution:
        return invalid_resolution

    monkeypatch.setattr(customer_workflow, "create_customer_resolution_from_documents", fake_create_customer_resolution_from_documents)

    response = client.post("/api/v1/support-sessions", json={"customer_id": "customer_001", "message": "My order notifications stopped arriving."})
    response_data = response.json()

    assert response.status_code == 201
    assert response_data["status"] == "engineer_escalation"
    assert response_data["resolution"] is None
    assert response_data["citations"] == []


@pytest.mark.skipif(os.getenv("RUN_REAL_MODEL_TEST") != "1", reason="Set RUN_REAL_MODEL_TEST=1 to call the real model")
def test_real_customer_conversation() -> None:
    first_response = client.post("/api/v1/support-sessions", json={"customer_id": "customer_001", "message": "The page does not work."})
    first_response_data = first_response.json()

    assert first_response.status_code == 201
    assert first_response_data["status"] == "waiting_for_customer"
    assert first_response_data["problem_details"]["affected_feature"]

    session_id = first_response_data["session_id"]
    second_response = client.post(f"/api/v1/support-sessions/{session_id}/messages", json={"message": "It is the billing page, and nothing happens when I press the download button."})
    second_response_data = second_response.json()
    saved_state = customer_workflow.get_customer_support_snapshot(session_id)

    assert second_response.status_code == 200
    assert second_response_data["session_id"] == session_id
    assert second_response_data["problem_details"]["affected_feature"]
    assert saved_state.values["turn_count"] == 2
