import pytest

from app import support_workflow
from app.db.models import Ticket
from app.handoff import SupportHandoff
from app.support_agent import SupportInvestigationResult
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
    handoff = SupportHandoff(
        support_session_id="session_001",
        customer_id="customer_001",
        issue_summary="订单通知返回 HTTP 401。",
        affected_feature="事件通知",
        customer_impact="客户无法收到订单通知。",
        approximate_start_time=None,
        environment_snapshot={},
        collected_facts=[],
        attempted_steps=[],
        citation_ids=[],
        remaining_questions=missing_information,
        handoff_reason="客户侧没有安全的解决方法。",
    )

    ticket = Ticket()
    ticket.id = 1
    ticket.support_session_id = handoff.support_session_id
    ticket.handoff = handoff.model_dump(mode="json")
    ticket.status = TicketStatus.WAITING_CUSTOMER.value if missing_information else TicketStatus.CLASSIFIED.value
    return ticket


def test_support_workflow_investigates_complete_ticket(monkeypatch: pytest.MonkeyPatch) -> None:
    ticket = build_ticket([])
    expected_result = SupportInvestigationResult(
        conclusion="客户接收端返回 HTTP 401，平台运行正常。",
        supporting_facts=["两次发送都返回 HTTP 401。", "平台运行正常。"],
        needs_escalation=False,
    )

    def fake_investigate_support_ticket(ticket_context: TicketContext) -> SupportInvestigationResult:
        assert ticket_context.id == ticket.id
        return expected_result

    monkeypatch.setattr(support_workflow, "SessionLocal", lambda: FakeDatabase(ticket))
    monkeypatch.setattr(support_workflow, "investigate_support_ticket", fake_investigate_support_ticket)

    response = support_workflow.run_support_investigation(ticket.id)

    assert response.outcome == "resolution"
    assert response.result == expected_result
    assert response.message is None


def test_support_workflow_requests_clarification_before_investigation(monkeypatch: pytest.MonkeyPatch) -> None:
    ticket = build_ticket(["接收地址"])

    def fail_investigation(ticket_context: TicketContext) -> SupportInvestigationResult:
        pytest.fail("Support Agent should not run when required information is missing")

    monkeypatch.setattr(support_workflow, "SessionLocal", lambda: FakeDatabase(ticket))
    monkeypatch.setattr(support_workflow, "investigate_support_ticket", fail_investigation)

    response = support_workflow.run_support_investigation(ticket.id)

    assert response.outcome == "clarification"
    assert response.result is None
    assert response.message == "还需要客户补充以下信息：接收地址"
