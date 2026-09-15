from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.customer_workflow import CustomerMessageRequest, SupportRequest, SupportResponse, continue_customer_support, start_customer_support
from app.db.database import SessionLocal, engine
from app.db.models import Ticket
from app.support_workflow import SupportInvestigationResponse, run_support_investigation
from app.tickets import TicketResponse

app = FastAPI(title="ResolveAI API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
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


@app.post("/api/v1/support-sessions", response_model=SupportResponse, status_code=201, tags=["support"])
def start_support_session(request: SupportRequest) -> SupportResponse:
    try:
        return start_customer_support(request.customer_id, request.message)
    except Exception as error:
        raise HTTPException(status_code=502, detail="Customer support failed") from error


@app.post("/api/v1/support-sessions/{session_id}/messages", response_model=SupportResponse, tags=["support"])
def continue_support_session(session_id: str, request: CustomerMessageRequest) -> SupportResponse:
    try:
        return continue_customer_support(session_id, request.message)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Support session not found") from error
    except Exception as error:
        raise HTTPException(status_code=502, detail="Customer support failed") from error


@app.get("/api/v1/tickets/{ticket_id}", response_model=TicketResponse, tags=["tickets"])
def read_ticket(ticket_id: int) -> TicketResponse:
    with SessionLocal() as database:
        ticket = database.scalar(select(Ticket).where(Ticket.id == ticket_id))

        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket not found")

        return TicketResponse.model_validate(ticket)


@app.post("/api/v1/tickets/{ticket_id}/investigations", response_model=SupportInvestigationResponse, tags=["investigations"])
def create_investigation(ticket_id: int) -> SupportInvestigationResponse:
    try:
        return run_support_investigation(ticket_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Ticket not found") from error
