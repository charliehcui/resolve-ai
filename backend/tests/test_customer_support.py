import os

import pytest
from fastapi.testclient import TestClient

from app import customer_workflow, main
from app.customer_agent import ProblemDetails

client = TestClient(main.app)


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

    monkeypatch.setattr(customer_workflow, "update_customer_problem", fake_update_customer_problem)
    monkeypatch.setattr(customer_workflow, "create_customer_question", fake_create_customer_question)

    first_response = client.post("/api/v1/support-sessions", json={"customer_id": "customer_001", "message": "A feature does not work."})
    session_id = first_response.json()["session_id"]
    second_response = client.post(f"/api/v1/support-sessions/{session_id}/messages", json={"message": "It is the invoice export feature."})
    second_response_data = second_response.json()
    saved_state = customer_workflow.customer_support_graph.get_state({"configurable": {"thread_id": session_id}})

    assert first_response.status_code == 201
    assert second_response.status_code == 200
    assert second_response_data["session_id"] == session_id
    assert second_response_data["problem_details"] == updated_problem_details.model_dump(mode="json")
    assert second_response_data["status"] == "ready_for_support"
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
