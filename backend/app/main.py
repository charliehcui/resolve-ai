from fastapi import FastAPI, HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.classification import ClassificationRequest, ClassificationResult, classify_ticket
from app.db.database import SessionLocal, engine
from app.db.models import Ticket
from app.tickets import TicketCreate, TicketResponse, TicketStatus
from app.workflow import InvestigationResponse, run_investigation

app = FastAPI(title="ResolveAI API", version="0.1.0")


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


@app.post("/api/v1/classification", response_model=ClassificationResult, tags=["classification"])
def create_classification(request: ClassificationRequest) -> ClassificationResult:
    try:
        return classify_ticket(request)
    except Exception as error:
        raise HTTPException(status_code=502, detail="Ticket classification failed") from error


@app.post("/api/v1/tickets", response_model=TicketResponse, status_code=201, tags=["tickets"])
def create_ticket(request: TicketCreate) -> TicketResponse:
    try:
        classification = classify_ticket(request)
    except Exception as error:
        raise HTTPException(status_code=502, detail="Ticket classification failed") from error

    if classification.missing_information:
        ticket_status = TicketStatus.WAITING_CUSTOMER
    else:
        ticket_status = TicketStatus.CLASSIFIED

    ticket = Ticket()
    ticket.customer_id = request.customer_id
    ticket.title = request.title
    ticket.description = request.description
    ticket.classification = classification.model_dump(mode="json")
    ticket.status = ticket_status.value

    with SessionLocal() as database:
        database.add(ticket)
        database.commit()
        database.refresh(ticket)
        return TicketResponse.model_validate(ticket)


@app.get("/api/v1/tickets/{ticket_id}", response_model=TicketResponse, tags=["tickets"])
def read_ticket(ticket_id: int) -> TicketResponse:
    with SessionLocal() as database:
        ticket = database.scalar(select(Ticket).where(Ticket.id == ticket_id))

        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket not found")

        return TicketResponse.model_validate(ticket)


@app.post("/api/v1/tickets/{ticket_id}/investigations", response_model=InvestigationResponse, tags=["investigations"])
def create_investigation(ticket_id: int) -> InvestigationResponse:
    try:
        return run_investigation(ticket_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Ticket not found") from error
