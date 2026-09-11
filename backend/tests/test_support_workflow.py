import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import support_workflow
from app.db.database import Base
from app.db.models import SupportSession, Ticket
from app.handoff import SupportHandoff
from app.support_agent import SupportInvestigationRun
from app.support_results import SupportInvestigationResult
from app.tickets import TicketContext, TicketStatus


@pytest.fixture
def test_database(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(support_workflow, "SessionLocal", test_session)

    yield test_session

    Base.metadata.drop_all(engine)
    engine.dispose()


def build_ticket(test_session, missing_information: list[str]) -> int:
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

    support_session = SupportSession(
        session_id="session_001",
        customer_id="customer_001",
        thread_id="thread_001",
        status="needs_assistance",
        final_problem_details=None,
        customer_result=None,
    )
    ticket = Ticket(
        support_session_id=handoff.support_session_id,
        handoff=handoff.model_dump(mode="json"),
        investigation_result=None,
        investigation_tools=None,
        status=TicketStatus.OPEN.value,
    )

    with test_session() as database:
        database.add(support_session)
        database.add(ticket)
        database.commit()
        return ticket.id


def test_support_workflow_investigates_complete_ticket(monkeypatch: pytest.MonkeyPatch, test_database) -> None:
    ticket_id = build_ticket(test_database, [])
    expected_result = SupportInvestigationResult(
        conclusion="客户接收端返回 HTTP 401，平台运行正常。",
        supporting_facts=["两次发送都返回 HTTP 401。", "平台运行正常。"],
        customer_explanation="通知已发出，但接收地址拒绝了请求。请检查接收端的访问设置。",
        outcome="resolution",
    )

    def fake_investigate_support_ticket(ticket_context: TicketContext) -> SupportInvestigationRun:
        assert ticket_context.id == ticket_id
        return SupportInvestigationRun(result=expected_result, tools_used=["get_event_notification_deliveries", "get_platform_status"])

    monkeypatch.setattr(support_workflow, "investigate_support_ticket", fake_investigate_support_ticket)

    response = support_workflow.run_support_investigation(ticket_id)

    assert response.result == expected_result
    assert response.tools_used == ["get_event_notification_deliveries", "get_platform_status"]

    with test_database() as database:
        saved_ticket = database.get(Ticket, ticket_id)
        saved_session = database.get(SupportSession, "session_001")

    assert saved_ticket is not None
    assert saved_ticket.status == TicketStatus.RESOLVED.value
    assert saved_ticket.investigation_result == expected_result.model_dump(mode="json")
    assert saved_ticket.investigation_tools == response.tools_used
    assert saved_session is not None
    assert saved_session.status == "support_resolved"
    assert saved_session.customer_result == expected_result.customer_explanation


def test_support_workflow_escalates_missing_information_without_reasking(monkeypatch: pytest.MonkeyPatch, test_database) -> None:
    ticket_id = build_ticket(test_database, ["接收地址"])

    def fail_investigation(ticket_context: TicketContext) -> SupportInvestigationRun:
        pytest.fail("Support Agent should not run when required information is missing")

    monkeypatch.setattr(support_workflow, "investigate_support_ticket", fail_investigation)

    response = support_workflow.run_support_investigation(ticket_id)

    assert response.result.outcome == "engineer_escalation"
    assert response.result.supporting_facts == []
    assert response.tools_used == []
    assert "不需要重复说明" in response.result.customer_explanation

    with test_database() as database:
        saved_ticket = database.get(Ticket, ticket_id)
        saved_session = database.get(SupportSession, "session_001")

    assert saved_ticket is not None
    assert saved_ticket.status == TicketStatus.ENGINEER_ESCALATION.value
    assert saved_session is not None
    assert saved_session.status == "engineer_escalation"
