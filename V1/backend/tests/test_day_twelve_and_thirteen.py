import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import simulator.app as simulator
from app import actions, main, resolvelab, support_sessions, support_workflow, tickets
from app.actions import ExecutableAction, evaluate_action_policy
from app.db.database import Base
from app.db.models import ActionExecutionRecord, ApprovalRecord, SupportSession, Ticket
from app.handoff import SupportHandoff
from app.support_agent import SupportInvestigationRun, support_investigation_tools
from app.support_evidence import create_internal_knowledge_evidence, create_tool_evidence
from app.support_results import ActionProposal, EvidenceItem, SupportDiagnosis
from app.tickets import TicketStatus


@pytest.fixture
def action_environment(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    simulator.reset_action_state()
    simulator_client = TestClient(simulator.app)

    for module in (actions, support_sessions, support_workflow, tickets):
        monkeypatch.setattr(module, "SessionLocal", test_session)

    def read_simulator(url: str, timeout: float):
        assert timeout == 5.0
        return simulator_client.get(url.removeprefix(resolvelab.settings.resolvelab_base_url))

    def write_simulator(url: str, json: dict[str, object], timeout: float):
        assert timeout == 5.0
        return simulator_client.post(url.removeprefix(resolvelab.settings.resolvelab_base_url), json=json)

    monkeypatch.setattr(resolvelab.httpx, "get", read_simulator)
    monkeypatch.setattr(resolvelab.httpx, "post", write_simulator)

    yield test_session

    simulator_client.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def create_action_ticket(test_session) -> tuple[int, SupportHandoff]:
    handoff = SupportHandoff(support_session_id="session_action", customer_id="customer_003", issue_summary="The report export failed after a dependency timeout.", affected_feature="report exports", customer_impact="The customer cannot download the report.", approximate_start_time=None, environment_snapshot={"product_version": "2026.8", "recent_activity": {"occurred_at": "2026-08-25T09:20:00Z"}}, collected_facts=[], attempted_steps=[], citation_ids=[], remaining_questions=[], handoff_reason="No safe customer-side resolution is available.")
    support_session = SupportSession(session_id="session_action", customer_id="customer_003", thread_id="session_action", status="needs_assistance", final_problem_details=None, customer_result=None)
    ticket = Ticket(support_session_id="session_action", handoff=handoff.model_dump(mode="json"), investigation_result=None, investigation_tools=None, status=TicketStatus.OPEN.value)

    with test_session() as database:
        database.add(support_session)
        database.add(ticket)
        database.commit()
        return ticket.id, handoff


def create_action_evidence(ticket_id: int) -> list[EvidenceItem]:
    operation = {"customer_id": "customer_003", "operation_id": "export_003", "feature": "report exports", "status": "failed", "failure_code": "dependency_timeout", "retry_allowed": True, "latest_run_status": "failed", "updated_at": "2026-08-25T09:20:00Z"}
    evidence = create_tool_evidence(ticket_id, "customer_003", "get_background_operation", operation)
    evidence.extend(create_tool_evidence(ticket_id, "customer_003", "get_platform_status", {"service": "report_exports", "status": "operational", "updated_at": "2026-08-25T09:25:00Z"}))
    document = {"chunk_id": "docs/internal/report-export-timeout.md:0", "source_uri": "docs/internal/report-export-timeout.md", "version": "2026.8", "content": "A dependency timeout with retry allowed is eligible for a human-controlled retry.", "score": 0.95, "visibility": "INTERNAL", "feature": "report exports", "effective_from": "2026-08-01", "effective_to": None}
    evidence.extend(create_internal_knowledge_evidence(ticket_id, "customer_003", "2026.8", "report exports", [document]))
    return evidence


def create_action_diagnosis(evidence: list[EvidenceItem]) -> SupportDiagnosis:
    evidence_ids = [item.evidence_id for item in evidence]
    proposal = ActionProposal(action_name="retry_failed_operation", reason="The failed export is eligible for one controlled retry.", supporting_evidence_ids=evidence_ids, intended_target_reference="export_003", expected_result="The report export operation reaches a succeeded state.", verification_method="read_background_operation")
    return SupportDiagnosis(conclusion="The report export failed after a temporary dependency timeout.", root_cause="A temporary dependency timeout caused the export to fail.", supporting_evidence_ids=evidence_ids, confidence_band="high", resolution="Retry the failed report export after human approval.", action_proposal=proposal, customer_explanation="报表导出需要技术人员确认后重新处理，目前没有进行任何更改。", outcome="action_required")


def configure_action_investigation(monkeypatch: pytest.MonkeyPatch, ticket_id: int) -> tuple[list[EvidenceItem], SupportDiagnosis]:
    evidence = create_action_evidence(ticket_id)
    diagnosis = create_action_diagnosis(evidence)

    def fake_investigation(ticket_context) -> SupportInvestigationRun:
        assert ticket_context.id == ticket_id
        return SupportInvestigationRun(result=diagnosis, tools_used=["get_background_operation", "get_platform_status", "search_internal_knowledge"], evidence=evidence)

    monkeypatch.setattr(support_workflow, "investigate_support_ticket", fake_investigation)
    return evidence, diagnosis


def test_day_twelve_policy_rejects_missing_or_wrong_customer_evidence(action_environment) -> None:
    ticket_id, handoff = create_action_ticket(action_environment)
    evidence = create_action_evidence(ticket_id)
    proposal = create_action_diagnosis(evidence).action_proposal

    assert proposal is not None
    valid_policy = evaluate_action_policy(ticket_id, TicketStatus.OPEN.value, handoff, proposal, evidence)
    missing_evidence_proposal = proposal.model_copy(update={"supporting_evidence_ids": proposal.supporting_evidence_ids + ["missing-evidence"]})
    missing_evidence_policy = evaluate_action_policy(ticket_id, TicketStatus.OPEN.value, handoff, missing_evidence_proposal, evidence)
    wrong_customer_evidence = evidence.copy()
    wrong_customer_evidence[0] = wrong_customer_evidence[0].model_copy(update={"customer_id": "customer_999"})
    wrong_customer_policy = evaluate_action_policy(ticket_id, TicketStatus.OPEN.value, handoff, proposal, wrong_customer_evidence)

    assert valid_policy.status == "awaiting_approval"
    assert missing_evidence_policy.status == "rejected"
    assert wrong_customer_policy.status == "rejected"

    with action_environment() as database:
        assert database.scalar(select(func.count()).select_from(ActionExecutionRecord)) == 0


def test_day_twelve_agents_have_no_write_tool() -> None:
    tool_names = [current_tool.name for current_tool in support_investigation_tools]

    assert "retry_failed_operation" not in tool_names
    assert "execute_action" not in tool_names


def test_day_thirteen_restart_then_approve_resumes_executes_once_and_verifies(monkeypatch: pytest.MonkeyPatch, action_environment) -> None:
    ticket_id, _ = create_action_ticket(action_environment)
    configure_action_investigation(monkeypatch, ticket_id)

    pending = support_workflow.run_support_investigation(ticket_id)

    assert pending.status == TicketStatus.AWAITING_APPROVAL
    assert pending.proposal_id is not None
    assert simulator.retry_execution_counts == {}

    config = {"configurable": {"thread_id": f"support-ticket-{ticket_id}"}}
    with support_workflow.checkpointing.open_postgres_checkpointer() as checkpointer:
        restarted_graph = support_workflow.build_support_investigation_graph(checkpointer)
        restarted_state = restarted_graph.get_state(config)

    assert len(restarted_state.interrupts) == 1
    assert restarted_state.values["proposal_id"] == pending.proposal_id

    with action_environment() as database:
        assert database.scalar(select(func.count()).select_from(ActionExecutionRecord)) == 0

    with TestClient(main.app) as client:
        first_response = client.post(f"/api/v1/action-proposals/{pending.proposal_id}/approval", json={"decision": "approve", "reviewer_role": "demo_approver"})
        repeated_response = client.post(f"/api/v1/action-proposals/{pending.proposal_id}/approval", json={"decision": "approve", "reviewer_role": "demo_approver"})
        ticket_response = client.get(f"/api/v1/tickets/{ticket_id}")

    assert first_response.status_code == 200
    assert first_response.json()["status"] == TicketStatus.RESOLVED.value
    assert repeated_response.status_code == 200
    assert repeated_response.json() == first_response.json()
    assert ticket_response.json()["status"] == TicketStatus.RESOLVED.value
    assert ticket_response.json()["approval"]["decision"] == "approve"
    assert ticket_response.json()["action_execution"]["status"] == "VERIFIED"
    assert simulator.retry_execution_counts[f"ticket-{ticket_id}:retry_failed_operation:export_003"] == 1

    with action_environment() as database:
        saved_session = database.get(SupportSession, "session_action")
        assert database.scalar(select(func.count()).select_from(ApprovalRecord)) == 1
        assert database.scalar(select(func.count()).select_from(ActionExecutionRecord)) == 1

    assert saved_session is not None
    assert saved_session.status == "support_resolved"
    assert "处理成功" in saved_session.customer_result


def test_day_thirteen_reject_never_executes_action(monkeypatch: pytest.MonkeyPatch, action_environment) -> None:
    ticket_id, _ = create_action_ticket(action_environment)
    configure_action_investigation(monkeypatch, ticket_id)
    pending = support_workflow.run_support_investigation(ticket_id)

    with TestClient(main.app) as client:
        response = client.post(f"/api/v1/action-proposals/{pending.proposal_id}/approval", json={"decision": "reject", "reviewer_role": "demo_approver"})
        ticket_response = client.get(f"/api/v1/tickets/{ticket_id}")

    assert response.status_code == 200
    assert response.json()["status"] == TicketStatus.ENGINEER_ESCALATION.value
    assert ticket_response.json()["approval"]["decision"] == "reject"
    assert ticket_response.json()["action_execution"] is None
    assert simulator.retry_execution_counts == {}


def test_day_thirteen_unapproved_execution_is_blocked(monkeypatch: pytest.MonkeyPatch, action_environment) -> None:
    ticket_id, _ = create_action_ticket(action_environment)
    configure_action_investigation(monkeypatch, ticket_id)
    pending = support_workflow.run_support_investigation(ticket_id)
    action = ExecutableAction(action_name="retry_failed_operation", ticket_id=ticket_id, customer_id="customer_003", operation_id="export_003", idempotency_key=f"ticket-{ticket_id}:retry_failed_operation:export_003")

    with pytest.raises(RuntimeError, match="approved action proposal"):
        actions.execute_retry_action(pending.proposal_id, action)

    assert simulator.retry_execution_counts == {}


def test_day_thirteen_failed_verification_escalates(monkeypatch: pytest.MonkeyPatch, action_environment) -> None:
    ticket_id, _ = create_action_ticket(action_environment)
    configure_action_investigation(monkeypatch, ticket_id)
    pending = support_workflow.run_support_investigation(ticket_id)
    monkeypatch.setattr(support_workflow, "verify_retry_action", lambda action, execution: (False, {"status": "failed"}, "The operation still reports a failed state."))

    with TestClient(main.app) as client:
        response = client.post(f"/api/v1/action-proposals/{pending.proposal_id}/approval", json={"decision": "approve", "reviewer_role": "demo_approver"})
        ticket_response = client.get(f"/api/v1/tickets/{ticket_id}")

    assert response.status_code == 200
    assert response.json()["status"] == TicketStatus.ENGINEER_ESCALATION.value
    assert ticket_response.json()["action_execution"]["status"] == "VERIFICATION_FAILED"
    assert ticket_response.json()["investigation_result"]["escalation_package"] is not None
    assert simulator.retry_execution_counts[f"ticket-{ticket_id}:retry_failed_operation:export_003"] == 1
