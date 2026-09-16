from datetime import datetime
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.database import SessionLocal
from app.db.models import ActionExecutionRecord, ActionProposalRecord, ApprovalRecord, Ticket
from app.handoff import SupportHandoff
from app.resolvelab import get_resolvelab_data, post_resolvelab_data
from app.support_results import ActionProposal, EvidenceItem


class ExecutableAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_name: Literal["retry_failed_operation"]
    ticket_id: int = Field(gt=0)
    customer_id: str = Field(min_length=1, max_length=100)
    operation_id: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=200)


class ActionPolicyDecision(BaseModel):
    status: Literal["rejected", "awaiting_approval"]
    reason: str
    action: ExecutableAction | None = None


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    reviewer_role: Literal["demo_approver"]


class ActionProposalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ticket_id: int
    proposal: ActionProposal
    status: str
    policy_reason: str
    created_at: datetime
    updated_at: datetime


class ApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    proposal_id: int
    decision: Literal["approve", "reject"]
    reviewer_role: Literal["demo_approver"]
    created_at: datetime


class ActionExecutionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    proposal_id: int
    idempotency_key: str
    status: str
    request: dict[str, object]
    before_state: dict[str, object] | None
    after_state: dict[str, object] | None
    external_reference: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime


def read_background_operation(customer_id: str) -> dict[str, object]:
    result = get_resolvelab_data(f"/customers/{quote(customer_id, safe='')}/background-operation")

    if isinstance(result, dict):
        return result

    return {"status": "error", "error": {"code": "invalid_response", "message": "ResolveLab returned an unexpected data shape"}}


def reject_policy(reason: str) -> ActionPolicyDecision:
    return ActionPolicyDecision(status="rejected", reason=reason)


def evaluate_action_policy(ticket_id: int, ticket_status: str, handoff: SupportHandoff | None, proposal: ActionProposal, evidence: list[EvidenceItem]) -> ActionPolicyDecision:
    if proposal.action_name != "retry_failed_operation":
        return reject_policy("The proposed action is not allowed.")

    if handoff is None:
        return reject_policy("The ticket has no customer-bound handoff.")

    if ticket_status not in ("OPEN", "ACTION_REQUIRED", "AWAITING_APPROVAL"):
        return reject_policy("The ticket status does not allow this action.")

    evidence_by_id = {item.evidence_id: item for item in evidence}

    if any(evidence_id not in evidence_by_id for evidence_id in proposal.supporting_evidence_ids):
        return reject_policy("The proposal references missing evidence.")

    cited_evidence = [evidence_by_id[evidence_id] for evidence_id in proposal.supporting_evidence_ids]

    if any(item.ticket_id != ticket_id or item.customer_id != handoff.customer_id for item in cited_evidence):
        return reject_policy("The proposal evidence does not belong to the current ticket and customer.")

    operation_evidence = None
    latest_run_evidence = None
    platform_evidence = None
    document_evidence = None

    for item in cited_evidence:
        if item.source_reference.startswith("get_background_operation:") and item.facts.get("record_type") == "operation":
            operation_evidence = item
        elif item.source_reference.startswith("get_background_operation:") and item.facts.get("record_type") == "latest_run":
            latest_run_evidence = item
        elif item.source_reference.startswith("get_platform_status:") and item.facts.get("service") == "report_exports":
            platform_evidence = item
        elif item.source_type == "document":
            document_evidence = item

    if operation_evidence is None or latest_run_evidence is None or platform_evidence is None or document_evidence is None:
        return reject_policy("The proposal is missing required operation, platform, or document evidence.")

    operation_id = operation_evidence.facts.get("operation_id")

    if not isinstance(operation_id, str) or operation_id != proposal.intended_target_reference:
        return reject_policy("The proposed target does not match the supported operation.")

    if operation_evidence.facts.get("status") != "failed" or operation_evidence.facts.get("failure_code") != "dependency_timeout" or operation_evidence.facts.get("retry_allowed") is not True:
        return reject_policy("The supported operation is not eligible for retry.")

    if latest_run_evidence.facts.get("operation_id") != operation_id or latest_run_evidence.facts.get("status") != "failed":
        return reject_policy("The latest operation run is not in a retryable failed state.")

    if platform_evidence.facts.get("status") != "operational":
        return reject_policy("The report export service is not operational.")

    current_operation = read_background_operation(handoff.customer_id)

    if current_operation.get("status") == "error":
        return reject_policy("The current operation state could not be verified.")

    if current_operation.get("customer_id") != handoff.customer_id or current_operation.get("operation_id") != operation_id:
        return reject_policy("The current operation does not belong to the ticket customer and target.")

    if current_operation.get("feature") != "report exports" or current_operation.get("status") != "failed" or current_operation.get("latest_run_status") != "failed" or current_operation.get("failure_code") != "dependency_timeout" or current_operation.get("retry_allowed") is not True:
        return reject_policy("The current operation state no longer allows a retry.")

    try:
        action = ExecutableAction(action_name="retry_failed_operation", ticket_id=ticket_id, customer_id=handoff.customer_id, operation_id=operation_id, idempotency_key=f"ticket-{ticket_id}:retry_failed_operation:{operation_id}")
    except ValidationError:
        return reject_policy("The trusted action parameters did not pass validation.")

    return ActionPolicyDecision(status="awaiting_approval", reason="The action passed policy checks and requires human approval before execution.", action=action)


def save_action_proposal(ticket_id: int, proposal: ActionProposal, policy: ActionPolicyDecision) -> int:
    if policy.status != "awaiting_approval" or policy.action is None:
        raise ValueError("Only an action awaiting approval can be saved")

    with SessionLocal() as database:
        existing = database.scalar(select(ActionProposalRecord).where(ActionProposalRecord.ticket_id == ticket_id))

        if existing is not None:
            return existing.id

        record = ActionProposalRecord(ticket_id=ticket_id, proposal=proposal.model_dump(mode="json"), status="AWAITING_APPROVAL", policy_reason=policy.reason)

        try:
            database.add(record)
            database.commit()
            return record.id
        except IntegrityError:
            database.rollback()
            existing = database.scalar(select(ActionProposalRecord).where(ActionProposalRecord.ticket_id == ticket_id))

            if existing is None:
                raise

            return existing.id


def record_approval(proposal_id: int, request: ApprovalRequest) -> tuple[int, int]:
    with SessionLocal() as database:
        proposal = database.get(ActionProposalRecord, proposal_id)

        if proposal is None:
            raise ValueError("Action proposal not found")

        existing = database.scalar(select(ApprovalRecord).where(ApprovalRecord.proposal_id == proposal_id))

        if existing is not None:
            if existing.decision != request.decision or existing.reviewer_role != request.reviewer_role:
                raise RuntimeError("An approval decision has already been recorded")
            return existing.id, proposal.ticket_id

        if proposal.status != "AWAITING_APPROVAL":
            raise RuntimeError("Action proposal is not awaiting approval")

        approval = ApprovalRecord(proposal_id=proposal_id, decision=request.decision, reviewer_role=request.reviewer_role)
        proposal.status = "APPROVED" if request.decision == "approve" else "REJECTED"
        database.add(approval)
        database.commit()
        return approval.id, proposal.ticket_id


def recheck_approval(ticket_id: int, proposal_id: int, approval_id: int, evidence: list[EvidenceItem]) -> tuple[ApprovalResponse, ActionPolicyDecision]:
    with SessionLocal() as database:
        proposal_record = database.get(ActionProposalRecord, proposal_id)
        approval_record = database.get(ApprovalRecord, approval_id)
        ticket = database.get(Ticket, ticket_id)

        if proposal_record is None or approval_record is None or ticket is None:
            raise ValueError("Approval data not found")

        if proposal_record.ticket_id != ticket_id or approval_record.proposal_id != proposal_id:
            raise ValueError("Approval does not belong to the current proposal and ticket")

        if approval_record.reviewer_role != "demo_approver":
            raise ValueError("Approval reviewer role is not allowed")

        approval = ApprovalResponse.model_validate(approval_record)
        proposal = ActionProposal.model_validate(proposal_record.proposal)
        handoff = SupportHandoff.model_validate(ticket.handoff) if ticket.handoff is not None else None
        ticket_status = ticket.status

    if approval.decision == "reject":
        return approval, reject_policy("The action was rejected by the demo approver.")

    policy = evaluate_action_policy(ticket_id, ticket_status, handoff, proposal, evidence)

    if policy.status == "rejected":
        set_proposal_status(proposal_id, "POLICY_REJECTED", policy.reason)

    return approval, policy


def set_proposal_status(proposal_id: int, status: str, reason: str | None = None) -> None:
    with SessionLocal() as database:
        proposal = database.get(ActionProposalRecord, proposal_id)

        if proposal is None:
            raise ValueError("Action proposal not found")

        proposal.status = status

        if reason is not None:
            proposal.policy_reason = reason

        database.commit()


def execute_retry_action(proposal_id: int, action: ExecutableAction) -> ActionExecutionResponse:
    with SessionLocal() as database:
        proposal = database.get(ActionProposalRecord, proposal_id)
        approval = database.scalar(select(ApprovalRecord).where(ApprovalRecord.proposal_id == proposal_id))

        if proposal is None or approval is None:
            raise RuntimeError("An approved action proposal is required before execution")

        if proposal.status != "APPROVED" or approval.decision != "approve" or approval.reviewer_role != "demo_approver":
            raise RuntimeError("The action has not been approved for execution")

        saved_proposal = ActionProposal.model_validate(proposal.proposal)

        if proposal.ticket_id != action.ticket_id or saved_proposal.action_name != action.action_name or saved_proposal.intended_target_reference != action.operation_id:
            raise RuntimeError("The executable action does not match the approved proposal")

        execution = database.scalar(select(ActionExecutionRecord).where(ActionExecutionRecord.proposal_id == proposal_id))

        if execution is not None and execution.status in ("SUCCEEDED", "VERIFIED", "VERIFICATION_FAILED", "FAILED"):
            return ActionExecutionResponse.model_validate(execution)

        request_data = action.model_dump(mode="json")

        if execution is None:
            before_state = read_background_operation(action.customer_id)

            if before_state.get("customer_id") != action.customer_id or before_state.get("operation_id") != action.operation_id or before_state.get("status") != "failed" or before_state.get("latest_run_status") != "failed" or before_state.get("failure_code") != "dependency_timeout" or before_state.get("retry_allowed") is not True:
                execution = ActionExecutionRecord(proposal_id=proposal_id, idempotency_key=action.idempotency_key, status="FAILED", request=request_data, before_state=before_state, after_state=None, external_reference=None, error="The operation state no longer allows execution")
                proposal.status = "FAILED"
                database.add(execution)
                database.commit()
                return ActionExecutionResponse.model_validate(execution)

            execution = ActionExecutionRecord(proposal_id=proposal_id, idempotency_key=action.idempotency_key, status="STARTED", request=request_data, before_state=before_state, after_state=None, external_reference=None, error=None)
            database.add(execution)
            database.commit()

    result = post_resolvelab_data(f"/customers/{quote(action.customer_id, safe='')}/background-operations/{quote(action.operation_id, safe='')}/retry", {"idempotency_key": action.idempotency_key})

    with SessionLocal() as database:
        execution = database.scalar(select(ActionExecutionRecord).where(ActionExecutionRecord.proposal_id == proposal_id))
        proposal = database.get(ActionProposalRecord, proposal_id)

        if execution is None or proposal is None:
            raise RuntimeError("Action execution record not found")

        execution.after_state = result

        if result.get("status") == "error":
            execution.status = "FAILED"
            execution.error = "ResolveLab action execution failed"
            proposal.status = "FAILED"
        else:
            execution.status = "SUCCEEDED"
            external_reference = result.get("external_reference")
            execution.external_reference = str(external_reference) if external_reference is not None else None
            proposal.status = "EXECUTED"

        database.commit()
        return ActionExecutionResponse.model_validate(execution)


def verify_retry_action(action: ExecutableAction, execution: ActionExecutionResponse) -> tuple[bool, dict[str, object], str | None]:
    if execution.status not in ("SUCCEEDED", "VERIFIED"):
        return False, {}, execution.error or "The approved action did not complete successfully."

    current_operation = read_background_operation(action.customer_id)

    if current_operation.get("status") == "error":
        return False, current_operation, "The operation state could not be read after execution."

    verified = current_operation.get("customer_id") == action.customer_id and current_operation.get("operation_id") == action.operation_id and current_operation.get("status") == "succeeded" and current_operation.get("latest_run_status") == "succeeded"

    if verified is False:
        return False, current_operation, "The operation state does not confirm a successful retry."

    return True, current_operation, None


def save_verification_result(proposal_id: int, verified: bool, error: str | None) -> None:
    with SessionLocal() as database:
        proposal = database.get(ActionProposalRecord, proposal_id)
        execution = database.scalar(select(ActionExecutionRecord).where(ActionExecutionRecord.proposal_id == proposal_id))

        if proposal is None or execution is None:
            raise RuntimeError("Action execution record not found")

        if verified:
            proposal.status = "VERIFIED"
            execution.status = "VERIFIED"
            execution.error = None
        else:
            proposal.status = "VERIFICATION_FAILED"
            execution.status = "VERIFICATION_FAILED"
            execution.error = error

        database.commit()


def get_ticket_action_records(ticket_id: int) -> tuple[ActionProposalResponse | None, ApprovalResponse | None, ActionExecutionResponse | None]:
    with SessionLocal() as database:
        proposal = database.scalar(select(ActionProposalRecord).where(ActionProposalRecord.ticket_id == ticket_id))

        if proposal is None:
            return None, None, None

        approval = database.scalar(select(ApprovalRecord).where(ApprovalRecord.proposal_id == proposal.id))
        execution = database.scalar(select(ActionExecutionRecord).where(ActionExecutionRecord.proposal_id == proposal.id))
        proposal_response = ActionProposalResponse.model_validate(proposal)
        approval_response = ApprovalResponse.model_validate(approval) if approval is not None else None
        execution_response = ActionExecutionResponse.model_validate(execution) if execution is not None else None
        return proposal_response, approval_response, execution_response
