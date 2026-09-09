import os

import pytest
from fastapi.testclient import TestClient

from app import customer_workflow, main
from app.customer_agent import CustomerDocumentAnswer, ProblemDetails

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def set_default_customer_side_data(monkeypatch: pytest.MonkeyPatch) -> None:
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

    monkeypatch.setattr(customer_workflow, "get_current_product_context", fake_get_current_product_context)
    monkeypatch.setattr(customer_workflow, "should_get_recent_customer_activity", fake_should_get_recent_customer_activity)
    monkeypatch.setattr(customer_workflow, "update_customer_problem_with_customer_side_data", fake_update_customer_problem_with_customer_side_data)


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
    saved_state = customer_workflow.customer_support_graph.get_state({"configurable": {"thread_id": session_id}})

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

    def fake_create_customer_answer_from_documents(problem_details: ProblemDetails, customer_side_data: dict[str, object], retrieved_customer_documents: list[dict[str, object]]) -> CustomerDocumentAnswer:
        return CustomerDocumentAnswer(can_answer=True, answer="Save the destination again, then send one test notification.", citation_ids=["docs/customer/recovery.md:0"])

    monkeypatch.setattr(customer_workflow, "update_customer_problem", fake_update_customer_problem)
    monkeypatch.setattr(customer_workflow, "create_customer_question", fake_create_customer_question)
    monkeypatch.setattr(customer_workflow, "retrieve_documents_for_customer_question", FakeCustomerDocumentSearch())
    monkeypatch.setattr(customer_workflow, "create_customer_answer_from_documents", fake_create_customer_answer_from_documents)

    first_response = client.post("/api/v1/support-sessions", json={"customer_id": "customer_001", "message": "A feature does not work."})
    session_id = first_response.json()["session_id"]
    second_response = client.post(f"/api/v1/support-sessions/{session_id}/messages", json={"message": "It is the invoice export feature."})
    second_response_data = second_response.json()
    saved_state = customer_workflow.customer_support_graph.get_state({"configurable": {"thread_id": session_id}})

    assert first_response.status_code == 201
    assert second_response.status_code == 200
    assert second_response_data["session_id"] == session_id
    assert second_response_data["problem_details"] == updated_problem_details.model_dump(mode="json")
    assert second_response_data["customer_response"] == "Save the destination again, then send one test notification."
    assert second_response_data["citations"] == [{"chunk_id": "docs/customer/recovery.md:0", "source_uri": "docs/customer/recovery.md", "version": "2026.8"}]
    assert second_response_data["status"] == "answer_provided"
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
    saved_state = customer_workflow.customer_support_graph.get_state({"configurable": {"thread_id": session_id}})

    assert final_response.status_code == 200
    assert final_response.json()["status"] == "needs_assistance"
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
    saved_state = customer_workflow.customer_support_graph.get_state({"configurable": {"thread_id": session_id}})

    assert second_response.status_code == 200
    assert second_response.json()["status"] == "needs_assistance"
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
        "asked_questions": [],
        "missing_information": problem_details.missing_information,
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
    saved_state = customer_workflow.customer_support_graph.get_state({"configurable": {"thread_id": session_id}})

    assert response.status_code == 201
    assert response_data["customer_response"] == "When did this problem start? This will help me understand what may have changed."
    assert "version" not in response_data["customer_response"].lower()
    assert saved_state.values["customer_side_data"]["current_product_context"]["product_version"] == "2026.8"


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
    saved_state = customer_workflow.customer_support_graph.get_state({"configurable": {"thread_id": session_id}})

    assert second_response.status_code == 200
    assert second_response_data["session_id"] == session_id
    assert second_response_data["problem_details"]["affected_feature"]
    assert saved_state.values["turn_count"] == 2
