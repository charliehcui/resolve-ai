from typing import TypedDict
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from app.db.database import SessionLocal
from app.db.models import Ticket
from app.support_agent import SupportInvestigationResult, investigate_support_ticket
from app.tickets import TicketContext, build_ticket_context


class SupportCaseState(TypedDict):
    ticket_id: int
    ticket: TicketContext | None
    investigation_result: SupportInvestigationResult | None
    outcome: str | None
    error: str | None


class SupportInvestigationResponse(BaseModel):
    ticket_id: int
    outcome: str
    result: SupportInvestigationResult | None = None
    message: str | None = None


def load_ticket(state: SupportCaseState) -> dict[str, object]:
    with SessionLocal() as database:
        ticket = database.get(Ticket, state["ticket_id"])

        if ticket is None:
            raise ValueError("Ticket not found")

        return {"ticket": build_ticket_context(ticket)}


def route_after_load(state: SupportCaseState) -> str:
    ticket = state["ticket"]

    if ticket is None:
        return "finalize"

    if ticket.classification.missing_information:
        return "finalize"

    return "investigate"


def investigate(state: SupportCaseState) -> dict[str, object]:
    ticket = state["ticket"]

    if ticket is None:
        return {"error": "Ticket could not be loaded"}

    try:
        result = investigate_support_ticket(ticket)
    except Exception:
        return {"error": "Investigation failed"}

    return {"investigation_result": result}


def finalize(state: SupportCaseState) -> dict[str, object]:
    if state["error"] is not None:
        return {"outcome": "escalation"}

    ticket = state["ticket"]

    if ticket is None:
        return {"outcome": "escalation", "error": "Ticket could not be loaded"}

    if ticket.classification.missing_information:
        return {"outcome": "clarification"}

    result = state["investigation_result"]

    if result is None:
        return {"outcome": "escalation", "error": "Investigation produced no result"}

    if result.needs_escalation:
        return {"outcome": "escalation"}

    return {"outcome": "resolution"}


support_workflow_builder = StateGraph(SupportCaseState)
support_workflow_builder.add_node("load_ticket", load_ticket)
support_workflow_builder.add_node("investigate", investigate)
support_workflow_builder.add_node("finalize", finalize)
support_workflow_builder.add_edge(START, "load_ticket")
support_workflow_builder.add_conditional_edges(
    "load_ticket",
    route_after_load,
    {"investigate": "investigate", "finalize": "finalize"},
)
support_workflow_builder.add_edge("investigate", "finalize")
support_workflow_builder.add_edge("finalize", END)

support_investigation_workflow = support_workflow_builder.compile(checkpointer=InMemorySaver())


def run_support_investigation(ticket_id: int) -> SupportInvestigationResponse:
    initial_state: SupportCaseState = {
        "ticket_id": ticket_id,
        "ticket": None,
        "investigation_result": None,
        "outcome": None,
        "error": None,
    }

    config = {"configurable": {"thread_id": str(uuid4())}, "recursion_limit": 10}
    final_state = support_investigation_workflow.invoke(initial_state, config)
    outcome = final_state["outcome"]

    if outcome is None:
        raise RuntimeError("Support workflow did not produce an outcome")

    message = final_state["error"]
    ticket = final_state["ticket"]

    if outcome == "clarification" and ticket is not None:
        missing_information = ", ".join(ticket.classification.missing_information)
        message = f"More information is required: {missing_information}"

    return SupportInvestigationResponse(
        ticket_id=ticket_id,
        outcome=outcome,
        result=final_state["investigation_result"],
        message=message,
    )
