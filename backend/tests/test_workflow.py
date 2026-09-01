import pytest

from app import workflow
from app.agent import InvestigationResult
from app.classification import ClassificationResult, TicketCategory, TicketSeverity
from app.db.models import Ticket
from app.tickets import TicketContext, TicketStatus


class FakeDatabase:
    def __init__(self, ticket: Ticket):
        self.ticket = ticket

    def __enter__(self):
        return self

    def __exit__(self, exception_type, exception_value, traceback):
        return None

    def get(self, model, ticket_id: int) -> Ticket | None:
        assert model is Ticket

        if ticket_id != self.ticket.id:
            return None

        return self.ticket


def build_ticket(missing_information: list[str]) -> Ticket:
    classification = ClassificationResult(
        category=TicketCategory.EVENT_NOTIFICATION_FAILURE,
        severity=TicketSeverity.MEDIUM,
        affected_feature="event notifications",
        summary="Order notifications returned HTTP 401.",
        missing_information=missing_information,
        urgency_reason="Notifications are repeatedly failing.",
    )

    ticket = Ticket()
    ticket.id = 1
    ticket.customer_id = "customer_001"
    ticket.title = "Order notification failed"
    ticket.description = "Order notifications returned HTTP 401 twice."
    ticket.classification = classification.model_dump(mode="json")
    ticket.status = TicketStatus.WAITING_CUSTOMER.value if missing_information else TicketStatus.CLASSIFIED.value
    return ticket


def test_workflow_investigates_complete_ticket(monkeypatch: pytest.MonkeyPatch) -> None:
    ticket = build_ticket([])
    expected_result = InvestigationResult(
        conclusion="The customer destination returned HTTP 401 while the platform remained operational.",
        supporting_facts=["Two deliveries returned HTTP 401.", "The platform is operational."],
        needs_escalation=False,
    )

    def fake_investigate_ticket(ticket_context: TicketContext) -> InvestigationResult:
        assert ticket_context.id == ticket.id
        return expected_result

    monkeypatch.setattr(workflow, "SessionLocal", lambda: FakeDatabase(ticket))
    monkeypatch.setattr(workflow, "investigate_ticket", fake_investigate_ticket)

    response = workflow.run_investigation(ticket.id)

    assert response.outcome == "resolution"
    assert response.result == expected_result
    assert response.message is None


def test_workflow_requests_clarification_before_investigation(monkeypatch: pytest.MonkeyPatch) -> None:
    ticket = build_ticket(["destination URL"])

    def fail_investigation(ticket_context: TicketContext) -> InvestigationResult:
        pytest.fail("Investigation Agent should not run when required information is missing")

    monkeypatch.setattr(workflow, "SessionLocal", lambda: FakeDatabase(ticket))
    monkeypatch.setattr(workflow, "investigate_ticket", fail_investigation)

    response = workflow.run_investigation(ticket.id)

    assert response.outcome == "clarification"
    assert response.result is None
    assert response.message == "More information is required: destination URL"
