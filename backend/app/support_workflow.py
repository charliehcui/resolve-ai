from typing import TypedDict
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from app.db.database import SessionLocal
from app.db.models import SupportSession, Ticket
from app.handoff import SupportHandoff
from app.support_agent import investigate_support_ticket
from app.support_results import SupportInvestigationResult
from app.tickets import TicketContext, TicketStatus, build_ticket_context


class SupportCaseState(TypedDict):
    ticket_id: int
    ticket: TicketContext | None
    handoff: SupportHandoff | None
    investigation_result: SupportInvestigationResult | None
    tools_used: list[str]
    error: str | None


class SupportInvestigationResponse(BaseModel):
    ticket_id: int
    result: SupportInvestigationResult
    tools_used: list[str]


def load_handoff(state: SupportCaseState) -> dict[str, object]:
    with SessionLocal() as database:
        ticket = database.get(Ticket, state["ticket_id"])

        if ticket is None:
            raise ValueError("Ticket not found")

        ticket_context = build_ticket_context(ticket)
        return {"ticket": ticket_context, "handoff": ticket_context.handoff}


def route_after_handoff(state: SupportCaseState) -> str:
    handoff = state["handoff"]

    if handoff is None or len(handoff.remaining_questions) > 0:
        return "finalize_support_result"

    return "investigate"


def investigate(state: SupportCaseState) -> dict[str, object]:
    ticket = state["ticket"]

    if ticket is None:
        return {"error": "Ticket could not be loaded"}

    try:
        investigation = investigate_support_ticket(ticket)
    except Exception:
        return {"error": "Investigation failed"}

    return {"investigation_result": investigation.result, "tools_used": investigation.tools_used}


def finalize_support_result(state: SupportCaseState) -> dict[str, object]:
    if state["investigation_result"] is not None:
        return {}

    handoff = state["handoff"]

    if handoff is None:
        conclusion = "The ticket is missing a complete handoff, so no reliable conclusion can be reached."
    elif len(handoff.remaining_questions) > 0:
        conclusion = "The available information is insufficient for a reliable conclusion."
    else:
        conclusion = "The automated investigation could not produce a reliable conclusion."

    result = SupportInvestigationResult(
        conclusion=conclusion,
        supporting_facts=[],
        customer_explanation="我们暂时无法确认问题原因，已经交给工程师继续检查。你不需要重复说明已经提供的信息。",
        outcome="engineer_escalation",
    )
    return {"investigation_result": result}


support_workflow_builder = StateGraph(SupportCaseState)
support_workflow_builder.add_node("load_handoff", load_handoff)
support_workflow_builder.add_node("investigate", investigate)
support_workflow_builder.add_node("finalize_support_result", finalize_support_result)
support_workflow_builder.add_edge(START, "load_handoff")
support_workflow_builder.add_conditional_edges(
    "load_handoff",
    route_after_handoff,
    {"investigate": "investigate", "finalize_support_result": "finalize_support_result"},
)
support_workflow_builder.add_edge("investigate", "finalize_support_result")
support_workflow_builder.add_edge("finalize_support_result", END)

support_investigation_workflow = support_workflow_builder.compile(checkpointer=InMemorySaver())


def save_support_result(ticket_id: int, investigation_result: SupportInvestigationResult, tools_used: list[str]) -> None:
    with SessionLocal() as database:
        ticket = database.get(Ticket, ticket_id)

        if ticket is None:
            raise ValueError("Ticket not found")

        ticket.investigation_result = investigation_result.model_dump(mode="json")
        ticket.investigation_tools = tools_used

        if investigation_result.outcome == "resolution":
            ticket.status = TicketStatus.RESOLVED.value
            session_status = "support_resolved"
        elif investigation_result.outcome == "action_required":
            ticket.status = TicketStatus.ACTION_REQUIRED.value
            session_status = "action_required"
        else:
            ticket.status = TicketStatus.ENGINEER_ESCALATION.value
            session_status = "engineer_escalation"

        if ticket.support_session_id is not None:
            support_session = database.get(SupportSession, ticket.support_session_id)

            if support_session is None:
                raise ValueError("Support session not found")

            support_session.status = session_status
            support_session.customer_result = investigation_result.customer_explanation

        database.commit()


def run_support_investigation(ticket_id: int) -> SupportInvestigationResponse:
    initial_state: SupportCaseState = {
        "ticket_id": ticket_id,
        "ticket": None,
        "handoff": None,
        "investigation_result": None,
        "tools_used": [],
        "error": None,
    }

    config = {"configurable": {"thread_id": str(uuid4())}, "recursion_limit": 10}
    final_state = support_investigation_workflow.invoke(initial_state, config)
    investigation_result = final_state["investigation_result"]

    if investigation_result is None:
        raise RuntimeError("Support workflow did not produce a result")

    tools_used = final_state["tools_used"]
    save_support_result(ticket_id, investigation_result, tools_used)

    return SupportInvestigationResponse(ticket_id=ticket_id, result=investigation_result, tools_used=tools_used)
