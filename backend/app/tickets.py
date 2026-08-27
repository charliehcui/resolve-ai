from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.classification import ClassificationRequest, ClassificationResult
from app.db.models import Ticket


class TicketStatus(StrEnum):
    CLASSIFIED = "CLASSIFIED"
    WAITING_CUSTOMER = "WAITING_CUSTOMER"


class TicketCreate(ClassificationRequest):
    external_id: str = Field(min_length=1, max_length=100)


class TicketResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    organization_id: str
    external_id: str
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
    external_id: str
    customer_id: str | None
    title: str
    description: str
    classification: ClassificationResult
    status: TicketStatus

def build_ticket_context(ticket: Ticket) -> TicketContext:
    return TicketContext.model_validate(ticket)
