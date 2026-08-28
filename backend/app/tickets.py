from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.classification import ClassificationRequest, ClassificationResult
from app.db.models import Ticket


class TicketStatus(StrEnum):
    CLASSIFIED = "CLASSIFIED"
    WAITING_CUSTOMER = "WAITING_CUSTOMER"


class TicketCreate(ClassificationRequest):
    pass


class TicketResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: str | None
    title: str
    description: str
    classification: ClassificationResult
    status: TicketStatus
    created_at: datetime
    updated_at: datetime


class TicketContext(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: str | None
    title: str
    description: str
    classification: ClassificationResult
    status: TicketStatus


def build_ticket_context(ticket: Ticket) -> TicketContext:
    return TicketContext.model_validate(ticket)
