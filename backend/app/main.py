from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.classification import ClassificationRequest, ClassificationResult, classify_ticket
from app.db.database import engine, get_database_session
from app.tickets import (
    TicketCreate,
    TicketResponse,
    find_ticket_by_external_id,
    find_ticket_by_id,
    save_ticket,
)


def get_organization_id(
    x_organization_id: Annotated[
        str,
        Header(alias="X-Organization-Id", min_length=1, max_length=100),
    ],
) -> str:
    return x_organization_id


DatabaseSession = Annotated[Session, Depends(get_database_session)]
OrganizationId = Annotated[str, Depends(get_organization_id)]


app = FastAPI(
    title="ResolveAI API",
    version="0.1.0",
)


@app.get("/health/live", tags=["health"])
def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def health_ready() -> dict[str, str]:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="Database is not ready")

    return {"status": "ready"}


@app.post(
    "/api/v1/classification",
    response_model=ClassificationResult,
    tags=["classification"],
)
def create_classification(request: ClassificationRequest) -> ClassificationResult:
    try:
        return classify_ticket(request)
    except Exception as error:
        raise HTTPException(status_code=502, detail="Ticket classification failed") from error


@app.post(
    "/api/v1/tickets",
    response_model=TicketResponse,
    status_code=201,
    tags=["tickets"],
)
def create_ticket(
    request: TicketCreate,
    organization_id: OrganizationId,
    database: DatabaseSession,
) -> TicketResponse:
    existing_ticket = find_ticket_by_external_id(
        database=database,
        organization_id=organization_id,
        external_id=request.external_id,
    )

    if existing_ticket is not None:
        raise HTTPException(status_code=409, detail="Ticket external ID already exists")

    database.rollback()

    classification_request = ClassificationRequest(
        title=request.title,
        description=request.description,
        customer_id=request.customer_id,
    )

    try:
        classification = classify_ticket(classification_request)
    except Exception as error:
        raise HTTPException(status_code=502, detail="Ticket classification failed") from error

    try:
        ticket = save_ticket(
            database=database,
            organization_id=organization_id,
            request=request,
            classification=classification,
        )
    except IntegrityError as error:
        database.rollback()
        raise HTTPException(status_code=409, detail="Ticket external ID already exists") from error

    return TicketResponse.model_validate(ticket)


@app.get(
    "/api/v1/tickets/{ticket_id}",
    response_model=TicketResponse,
    tags=["tickets"],
)
def read_ticket(
    ticket_id: UUID,
    organization_id: OrganizationId,
    database: DatabaseSession,
) -> TicketResponse:
    ticket = find_ticket_by_id(
        database=database,
        organization_id=organization_id,
        ticket_id=ticket_id,
    )

    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")

    return TicketResponse.model_validate(ticket)