from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

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
    ENGINEER_ESCALATION = "ENGINEER_ESCALATION"


class TicketResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    support_session_id: str | None
    handoff: SupportHandoff | None
    investigation_result: SupportInvestigationResult | None
    investigation_tools: list[str] | None
    status: TicketStatus
    created_at: datetime
    updated_at: datetime


class TicketContext(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    support_session_id: str | None
    handoff: SupportHandoff | None
    status: TicketStatus


def build_ticket_context(ticket: Ticket) -> TicketContext:
    return TicketContext.model_validate(ticket)


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
