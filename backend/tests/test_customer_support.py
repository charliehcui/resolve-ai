import os

import pytest
from fastapi.testclient import TestClient

from app import customer_workflow, main
from app.customer_agent import ProblemDetails

client = TestClient(main.app)


def test_start_support_session(monkeypatch: pytest.MonkeyPatch) -> None:
    expected_problem_details = ProblemDetails(
        summary="The customer says a feature has not worked today.",
        product_area="unknown",
        problem="The feature does not work.",
        customer_goal="Use the feature normally.",
        missing_information=["feature name", "what happens when the feature is used"],
    )

    def fake_understand_customer_problem(customer_message: str) -> ProblemDetails:
        assert customer_message == "This feature has not worked all day."
        return expected_problem_details

    monkeypatch.setattr(customer_workflow, "understand_customer_problem", fake_understand_customer_problem)

    response = client.post(
        "/api/v1/support-sessions",
        json={"customer_id": "customer_001", "message": "This feature has not worked all day."},
    )

    response_data = response.json()
    session_id = response_data["session_id"]
    saved_state = customer_workflow.customer_support_graph.get_state({"configurable": {"thread_id": session_id}})
    expected_customer_response = f"Thanks. I understand the problem as: {expected_problem_details.summary} I have saved these details and can continue helping you."

    assert response.status_code == 201
    assert response_data["problem_details"] == expected_problem_details.model_dump(mode="json")
    assert response_data["customer_response"] == expected_customer_response
    assert response_data["status"] == "understood"
    assert saved_state.values["customer_message"] == "This feature has not worked all day."


@pytest.mark.skipif(os.getenv("RUN_REAL_MODEL_TEST") != "1", reason="Set RUN_REAL_MODEL_TEST=1 to call the real model")
def test_real_customer_message() -> None:
    response = client.post(
        "/api/v1/support-sessions",
        json={"customer_id": "customer_001", "message": "This feature has not worked all day."},
    )

    response_data = response.json()

    assert response.status_code == 201
    assert response_data["problem_details"]["summary"]
    assert response_data["problem_details"]["problem"]
    assert isinstance(response_data["problem_details"]["missing_information"], list)
