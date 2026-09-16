from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import checkpointing
from app.actions import ApprovalRequest, record_approval
from app.core.config import settings
from app.customer_workflow import CustomerMessageRequest, SupportRequest, SupportResponse, continue_customer_support, read_customer_support, start_customer_support
from app.db.database import engine
from app.support_workflow import SupportInvestigationResponse, resume_support_investigation, run_support_investigation
from app.tickets import TicketInvestigationResponse, TicketResponse, get_ticket, get_ticket_investigation


@asynccontextmanager
async def lifespan(app: FastAPI):
    checkpointing.setup_postgres_checkpointer()
    yield


app = FastAPI(title="ResolveAI API", version="0.1.0", lifespan=lifespan)

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


@app.get("/api/v1/support-sessions/{session_id}", response_model=SupportResponse, tags=["support"])
def read_support_session(session_id: str) -> SupportResponse:
    try:
        return read_customer_support(session_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Support session not found") from error
    except Exception as error:
        raise HTTPException(status_code=502, detail="Customer support failed") from error


@app.get("/api/v1/tickets/{ticket_id}", response_model=TicketResponse, tags=["tickets"])
def read_ticket(ticket_id: int) -> TicketResponse:
    try:
        return get_ticket(ticket_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Ticket not found") from error


@app.post("/api/v1/tickets/{ticket_id}/investigations", response_model=SupportInvestigationResponse, tags=["investigations"])
def create_investigation(ticket_id: int) -> SupportInvestigationResponse:
    try:
        return run_support_investigation(ticket_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Ticket not found") from error


@app.get("/api/v1/tickets/{ticket_id}/investigation", response_model=TicketInvestigationResponse, tags=["investigations"])
def read_investigation(ticket_id: int) -> TicketInvestigationResponse:
    try:
        return get_ticket_investigation(ticket_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Ticket not found") from error


@app.post("/api/v1/action-proposals/{proposal_id}/approval", response_model=SupportInvestigationResponse, tags=["approvals"])
def decide_action_proposal(proposal_id: int, request: ApprovalRequest) -> SupportInvestigationResponse:
    try:
        approval_id, ticket_id = record_approval(proposal_id, request)
        return resume_support_investigation(ticket_id, proposal_id, approval_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Action proposal not found") from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail="Action proposal cannot be resumed") from error
