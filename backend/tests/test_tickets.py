from fastapi.testclient import TestClient
from sqlalchemy import delete

from app import main
from app.classification import ClassificationRequest, ClassificationResult, TicketCategory, TicketSeverity
from app.db.database import SessionLocal
from app.db.models import Ticket

client = TestClient(main.app)


def test_create_and_read_ticket(monkeypatch) -> None:
    expected_result = ClassificationResult(
        category=TicketCategory.EVENT_NOTIFICATION_FAILURE,
        severity=TicketSeverity.MEDIUM,
        affected_feature="event notifications",
        summary="Notifications returned HTTP 401.",
        missing_information=[],
        urgency_reason="Notifications are repeatedly failing.",
    )

    def fake_classify_ticket(request: ClassificationRequest) -> ClassificationResult:
        return expected_result

    monkeypatch.setattr(main, "classify_ticket", fake_classify_ticket)

    payload = {
        "customer_id": "customer_001",
        "title": "Order notification failed",
        "description": "Order notifications returned HTTP 401.",
    }

    ticket_id = None

    try:
        create_response = client.post("/api/v1/tickets", json=payload)

        assert create_response.status_code == 201

        created_ticket = create_response.json()
        ticket_id = created_ticket["id"]

        assert created_ticket["classification"] == expected_result.model_dump(mode="json")
        assert created_ticket["status"] == "CLASSIFIED"

        read_response = client.get(f"/api/v1/tickets/{ticket_id}")

        assert read_response.status_code == 200
        assert read_response.json() == created_ticket
    finally:
        if ticket_id is not None:
            with SessionLocal() as database:
                database.execute(delete(Ticket).where(Ticket.id == ticket_id))
                database.commit()
