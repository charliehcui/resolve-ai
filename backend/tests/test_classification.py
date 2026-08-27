import pytest
from app import main
from app.classification import ClassificationRequest, ClassificationResult, TicketCategory, TicketSeverity
from fastapi.testclient import TestClient

client = TestClient(main.app)


def test_classification_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    expected_result = ClassificationResult(
        category=TicketCategory.EVENT_NOTIFICATION_FAILURE,
        severity=TicketSeverity.MEDIUM,
        affected_feature="event notifications",
        summary="Order completion notifications receive HTTP 401 responses.",
        missing_information=[],
        urgency_reason="Notifications are repeatedly failing for one customer.",
    )

    def fake_classify_ticket(request: ClassificationRequest) -> ClassificationResult:
        assert request.customer_id == "customer_001"
        return expected_result

    monkeypatch.setattr(main, "classify_ticket", fake_classify_ticket)

    response = client.post(
        "/api/v1/classification",
        json={
            "title": "Order notification failed",
            "description": "Order order_1001 notifications returned HTTP 401 twice.",
            "customer_id": "customer_001",
        },
    )

    assert response.status_code == 200
    assert response.json() == expected_result.model_dump(mode="json")
