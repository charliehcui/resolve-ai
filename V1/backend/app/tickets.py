from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.actions import ActionExecutionResponse, ActionProposalResponse, ApprovalResponse, get_ticket_action_records
from app.db.database import SessionLocal
from app.db.models import SupportSession, Ticket
from app.handoff import SupportHandoff
from app.support_results import SupportInvestigationResult


class TicketStatus(StrEnum):
    OPEN = "OPEN"
    CLASSIFIED = "CLASSIFIED"
    WAITING_CUSTOMER = "WAITING_CUSTOMER"
    RESOLVED = "RESOLVED"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    ENGINEER_ESCALATION = "ENGINEER_ESCALATION"


class TicketResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    support_session_id: str | None
    handoff: SupportHandoff | None
    investigation_result: SupportInvestigationResult | None
    investigation_tools: list[str] | None
    action_proposal: ActionProposalResponse | None = None
    approval: ApprovalResponse | None = None
    action_execution: ActionExecutionResponse | None = None
    status: TicketStatus
    created_at: datetime
    updated_at: datetime


class TicketInvestigationResponse(BaseModel):
    ticket_id: int
    status: TicketStatus
    result: SupportInvestigationResult | None
    tools_used: list[str]
    action_proposal: ActionProposalResponse | None = None
    approval: ApprovalResponse | None = None
    action_execution: ActionExecutionResponse | None = None


class TicketContext(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    support_session_id: str | None
    handoff: SupportHandoff | None
    status: TicketStatus
    created_at: datetime | None = None


def build_ticket_context(ticket: Ticket) -> TicketContext:
    return TicketContext.model_validate(ticket)


def get_ticket_id_for_support_session(session_id: str) -> int | None:
    with SessionLocal() as database:
        ticket = database.scalar(select(Ticket).where(Ticket.support_session_id == session_id))

        if ticket is None:
            return None

        return ticket.id


def get_ticket(ticket_id: int) -> TicketResponse:
    with SessionLocal() as database:
        ticket = database.get(Ticket, ticket_id)

        if ticket is None:
            raise ValueError("Ticket not found")

        response = TicketResponse.model_validate(ticket)

    proposal, approval, execution = get_ticket_action_records(ticket_id)
    return response.model_copy(update={"action_proposal": proposal, "approval": approval, "action_execution": execution})


def get_ticket_investigation(ticket_id: int) -> TicketInvestigationResponse:
    with SessionLocal() as database:
        ticket = database.get(Ticket, ticket_id)

        if ticket is None:
            raise ValueError("Ticket not found")

        result = None

        if ticket.investigation_result is not None:
            result = SupportInvestigationResult.model_validate(ticket.investigation_result)

        status = TicketStatus(ticket.status)
        tools_used = ticket.investigation_tools or []

    proposal, approval, execution = get_ticket_action_records(ticket_id)
    return TicketInvestigationResponse(ticket_id=ticket_id, status=status, result=result, tools_used=tools_used, action_proposal=proposal, approval=approval, action_execution=execution)


def create_ticket_from_handoff(handoff: SupportHandoff) -> int:
    with SessionLocal() as database:
        existing_ticket = database.scalar(select(Ticket).where(Ticket.support_session_id == handoff.support_session_id))

        if existing_ticket is not None:
            return existing_ticket.id

        support_session = database.get(SupportSession, handoff.support_session_id)

        if support_session is None:
            raise ValueError("Support session not found")

        if support_session.customer_id != handoff.customer_id:
            raise ValueError("Support handoff customer does not match the support session")

        ticket = Ticket()
        ticket.support_session_id = handoff.support_session_id
        ticket.handoff = handoff.model_dump(mode="json")
        ticket.status = TicketStatus.OPEN.value

        try:
            database.add(ticket)
            database.flush()
            support_session.status = "needs_assistance"
            database.commit()
            return ticket.id
        except IntegrityError:
            database.rollback()
            existing_ticket = database.scalar(select(Ticket).where(Ticket.support_session_id == handoff.support_session_id))

            if existing_ticket is None:
                raise

            return existing_ticket.id
