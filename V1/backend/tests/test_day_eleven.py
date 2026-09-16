import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import actions, customer_workflow, main, support_sessions, support_workflow, tickets
from app.customer_agent import ProblemDetails
from app.db.database import Base
from app.db.models import SupportSession, Ticket
from app.handoff import SupportHandoff, SupportHandoffSummary
from app.support_agent import SupportInvestigationRun
from app.support_results import SupportDiagnosis, SupportInvestigationResult
from app.support_workflow import SupportInvestigationResponse
from app.tickets import TicketStatus


@pytest.fixture
def day_eleven_database(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

    for module in (actions, support_sessions, support_workflow, tickets):
        monkeypatch.setattr(module, "SessionLocal", test_session)

    yield test_session

    Base.metadata.drop_all(engine)
    engine.dispose()


def test_customer_session_restores_messages_and_does_not_repeat_handoff(monkeypatch: pytest.MonkeyPatch, day_eleven_database) -> None:
    investigation_count = 0
    incomplete_problem = ProblemDetails(summary="订单通知收不到。", affected_feature="order notifications", problem="Order notifications are not arriving.", customer_goal="恢复接收订单通知。", missing_information=["start time"])
    complete_problem = incomplete_problem.model_copy(update={"missing_information": [], "summary": "订单通知从今天开始收不到。"})

    def fake_update_customer_problem(messages: list[str], current_problem: ProblemDetails | None) -> ProblemDetails:
        if current_problem is None:
            return incomplete_problem
        return complete_problem

    def fake_run_support_investigation(ticket_id: int) -> SupportInvestigationResponse:
        nonlocal investigation_count
        investigation_count += 1
        result = SupportInvestigationResult(conclusion="An engineer should continue the investigation.", customer_explanation="我们暂时无法确认问题原因，已经交给工程师继续检查。你不需要重复说明已经提供的信息。", outcome="engineer_escalation")
        return SupportInvestigationResponse(ticket_id=ticket_id, result=result, tools_used=[])

    monkeypatch.setattr(customer_workflow, "update_customer_problem", fake_update_customer_problem)
    monkeypatch.setattr(customer_workflow, "get_current_product_context", lambda customer_id: {"account_status": "active", "product_version": "2026.8", "affected_feature": "order notifications", "feature_enabled": True})
    monkeypatch.setattr(customer_workflow, "should_get_recent_customer_activity", lambda problem, context: False)
    monkeypatch.setattr(customer_workflow, "update_customer_problem_with_customer_side_data", lambda problem, data: problem)
    monkeypatch.setattr(customer_workflow, "create_customer_question", lambda problem, questions: "这个问题是从什么时候开始的？")
    monkeypatch.setattr(customer_workflow, "retrieve_documents_for_customer_question", type("EmptySearch", (), {"invoke": lambda self, search_input: []})())
    monkeypatch.setattr(customer_workflow, "create_support_handoff_summary", lambda problem, messages: SupportHandoffSummary(issue_summary="Order notifications are not arriving.", customer_impact="The customer cannot receive order updates.", approximate_start_time="Today"))
    monkeypatch.setattr(customer_workflow, "run_support_investigation", fake_run_support_investigation)

    with TestClient(main.app) as client:
        first_response = client.post("/api/v1/support-sessions", json={"customer_id": "customer_001", "message": "我的订单通知收不到了。"})
        first_data = first_response.json()
        session_id = first_data["session_id"]
        restored_response = client.get(f"/api/v1/support-sessions/{session_id}")
        continued_response = client.post(f"/api/v1/support-sessions/{session_id}/messages", json={"message": "今天开始的。"})
        restored_handoff_response = client.get(f"/api/v1/support-sessions/{session_id}")
        repeated_response = client.post(f"/api/v1/support-sessions/{session_id}/messages", json={"message": "请继续。"})

    assert first_response.status_code == 201
    assert restored_response.status_code == 200
    assert restored_response.json()["messages"] == first_data["messages"]
    assert restored_response.json()["messages"] == [{"role": "customer", "content": "我的订单通知收不到了。"}, {"role": "assistant", "content": "这个问题是从什么时候开始的？"}]
    assert continued_response.status_code == 200
    assert restored_handoff_response.status_code == 200
    assert restored_handoff_response.json()["messages"] == continued_response.json()["messages"]
    assert restored_handoff_response.json()["problem_details"] == continued_response.json()["problem_details"]
    assert restored_handoff_response.json()["citations"] == continued_response.json()["citations"]
    assert restored_handoff_response.json()["ticket_id"] == continued_response.json()["ticket_id"]
    assert restored_handoff_response.json()["status"] == continued_response.json()["status"]
    assert repeated_response.status_code == 200
    assert repeated_response.json()["ticket_id"] == continued_response.json()["ticket_id"]
    assert investigation_count == 1

    with day_eleven_database() as database:
        saved_session = database.get(SupportSession, session_id)
        ticket_count = database.scalar(select(func.count()).select_from(Ticket))

    assert saved_session is not None
    assert saved_session.thread_id == session_id
    assert saved_session.customer_result == continued_response.json()["customer_response"]
    assert ticket_count == 1


def test_support_graph_resumes_after_completed_investigation_step(monkeypatch: pytest.MonkeyPatch, day_eleven_database, use_test_checkpointer) -> None:
    support_sessions.create_support_session_record("session_001", "customer_001", "session_001")
    handoff = SupportHandoff(support_session_id="session_001", customer_id="customer_001", issue_summary="Order notifications are not arriving.", affected_feature="order notifications", customer_impact="The customer cannot receive order updates.", approximate_start_time="Today", environment_snapshot={"product_version": "2026.8"}, collected_facts=[], attempted_steps=[], citation_ids=[], remaining_questions=[], handoff_reason="No safe customer-side resolution is available.")
    ticket_id = tickets.create_ticket_from_handoff(handoff)
    investigation_count = 0
    save_count = 0

    def fake_investigate_support_ticket(ticket_context) -> SupportInvestigationRun:
        nonlocal investigation_count
        investigation_count += 1
        diagnosis = SupportDiagnosis(conclusion="The available evidence is incomplete.", escalation_reason="An engineer must continue the investigation.", customer_explanation="我们暂时无法确认问题原因，已经交给工程师继续检查。", outcome="engineer_escalation")
        return SupportInvestigationRun(result=diagnosis, tools_used=["get_platform_status"])

    original_save_support_result = support_workflow.save_support_result

    def count_save_support_result(ticket_id: int, result: SupportInvestigationResult, tools_used: list[str]) -> None:
        nonlocal save_count
        save_count += 1
        original_save_support_result(ticket_id, result, tools_used)

    monkeypatch.setattr(support_workflow, "investigate_support_ticket", fake_investigate_support_ticket)
    monkeypatch.setattr(support_workflow, "save_support_result", count_save_support_result)
    interrupted_graph = support_workflow.support_workflow_builder.compile(checkpointer=use_test_checkpointer, interrupt_after=["investigate"])
    initial_state = {"ticket_id": ticket_id, "ticket": None, "handoff": None, "investigation_result": None, "evidence": [], "tool_errors": [], "tools_used": [], "error": None}
    config = {"configurable": {"thread_id": f"support-ticket-{ticket_id}"}, "recursion_limit": 10}

    interrupted_graph.invoke(initial_state, config)
    saved_state = interrupted_graph.get_state(config)

    assert saved_state.next == ("validate_evidence",)
    assert investigation_count == 1

    first_result = support_workflow.run_support_investigation(ticket_id)
    second_result = support_workflow.run_support_investigation(ticket_id)

    with TestClient(main.app) as client:
        investigation_response = client.get(f"/api/v1/tickets/{ticket_id}/investigation")

    assert first_result == second_result
    assert investigation_count == 1
    assert save_count == 1
    assert investigation_response.status_code == 200
    assert investigation_response.json()["ticket_id"] == ticket_id
    assert investigation_response.json()["status"] == TicketStatus.ENGINEER_ESCALATION.value
    assert investigation_response.json()["result"] == first_result.result.model_dump(mode="json")
    assert investigation_response.json()["tools_used"] == ["get_platform_status"]

    with day_eleven_database() as database:
        assert database.scalar(select(func.count()).select_from(Ticket)) == 1


def test_recovery_endpoints_return_not_found(day_eleven_database) -> None:
    with TestClient(main.app) as client:
        session_response = client.get("/api/v1/support-sessions/missing")
        investigation_response = client.get("/api/v1/tickets/999/investigation")

    assert session_response.status_code == 404
    assert session_response.json() == {"detail": "Support session not found"}
    assert investigation_response.status_code == 404
    assert investigation_response.json() == {"detail": "Ticket not found"}
