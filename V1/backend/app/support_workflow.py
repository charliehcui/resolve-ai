from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel

from app import checkpointing
from app.actions import ActionExecutionResponse, ActionPolicyDecision, ExecutableAction, evaluate_action_policy, execute_retry_action, get_ticket_action_records, recheck_approval, save_action_proposal, save_verification_result, verify_retry_action
from app.db.database import SessionLocal
from app.db.models import SupportSession, Ticket
from app.handoff import SupportHandoff
from app.support_agent import investigate_support_ticket
from app.support_evidence import build_engineer_escalation_package, validate_support_evidence
from app.support_results import ActionProposal, EvidenceItem, SupportDiagnosis, SupportInvestigationResult
from app.tickets import TicketContext, TicketStatus, build_ticket_context


class SupportCaseState(TypedDict):
    ticket_id: int
    ticket: TicketContext | None
    handoff: SupportHandoff | None
    investigation_result: SupportDiagnosis | SupportInvestigationResult | None
    evidence: list[EvidenceItem]
    tool_errors: list[str]
    tools_used: list[str]
    action_proposal: ActionProposal | None
    policy_decision: ActionPolicyDecision | None
    proposal_id: int | None
    approval_id: int | None
    executable_action: ExecutableAction | None
    action_execution: ActionExecutionResponse | None
    error: str | None


class SupportInvestigationResponse(BaseModel):
    ticket_id: int
    result: SupportInvestigationResult
    tools_used: list[str]
    status: TicketStatus = TicketStatus.OPEN
    proposal_id: int | None = None


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
        return "validate_evidence"

    return "investigate"


def investigate(state: SupportCaseState) -> dict[str, object]:
    ticket = state["ticket"]

    if ticket is None:
        return {"error": "Ticket could not be loaded"}

    try:
        investigation = investigate_support_ticket(ticket)
    except Exception:
        return {"error": "Investigation failed"}

    return {"investigation_result": investigation.result, "tools_used": investigation.tools_used, "evidence": investigation.evidence, "tool_errors": investigation.tool_errors}


def validate_evidence(state: SupportCaseState) -> dict[str, object]:
    ticket = state["ticket"]
    if ticket is None:
        raise ValueError("Ticket could not be loaded")
    handoff = state["handoff"]
    diagnosis = state["investigation_result"]

    if handoff is None:
        conclusion = "The ticket is missing a complete handoff, so no reliable conclusion can be reached."
    elif len(handoff.remaining_questions) > 0:
        conclusion = "The available information is insufficient for a reliable conclusion."
    else:
        conclusion = "The automated investigation could not produce a reliable conclusion."

    if diagnosis is None:
        diagnosis = SupportDiagnosis(conclusion=conclusion, customer_explanation="我们暂时无法确认问题原因，已经交给工程师继续检查。你不需要重复说明已经提供的信息。", escalation_reason=conclusion, outcome="engineer_escalation")

    result = validate_support_evidence(ticket, diagnosis, state["evidence"])
    return {"investigation_result": result, "action_proposal": result.action_proposal}


def route_after_validation(state: SupportCaseState) -> str:
    result = state["investigation_result"]

    if isinstance(result, SupportInvestigationResult) and result.outcome == "action_required":
        return "evaluate_action"

    return "finalize_support_result"


def evaluate_action(state: SupportCaseState) -> dict[str, object]:
    result = state["investigation_result"]
    proposal = state["action_proposal"]
    ticket = state["ticket"]

    if not isinstance(result, SupportInvestigationResult) or proposal is None or ticket is None:
        raise RuntimeError("A validated action proposal and ticket are required")

    policy = evaluate_action_policy(ticket.id, ticket.status.value, ticket.handoff, proposal, state["evidence"])

    if policy.status == "rejected":
        rejected_result = result.model_copy(update={"outcome": "engineer_escalation", "resolution": None, "escalation_reason": policy.reason, "customer_explanation": "目前不能安全执行这项操作，工单已经交给工程师继续检查。系统没有进行任何更改。"})
        return {"investigation_result": rejected_result, "policy_decision": policy}

    waiting_result = result.model_copy(update={"customer_explanation": "已经找到一个可能解决问题的操作，但需要技术人员确认后才能执行。目前没有进行任何更改。"})
    return {"investigation_result": waiting_result, "policy_decision": policy}


def route_after_policy(state: SupportCaseState) -> str:
    policy = state["policy_decision"]

    if policy is not None and policy.status == "awaiting_approval" and policy.action is not None:
        return "save_action_proposal"

    return "finalize_support_result"


def save_proposed_action(state: SupportCaseState) -> dict[str, object]:
    proposal = state["action_proposal"]
    policy = state["policy_decision"]

    if proposal is None or policy is None:
        raise RuntimeError("Action proposal or policy decision is missing")

    proposal_id = save_action_proposal(state["ticket_id"], proposal, policy)
    return {"proposal_id": proposal_id}


def wait_for_approval(state: SupportCaseState) -> dict[str, object]:
    proposal = state["action_proposal"]
    proposal_id = state["proposal_id"]

    if proposal is None or proposal_id is None:
        raise RuntimeError("Action proposal is missing")

    approval = interrupt({"proposal_id": proposal_id, "ticket_id": state["ticket_id"], "action_name": proposal.action_name, "reason": proposal.reason, "intended_target_reference": proposal.intended_target_reference, "expected_result": proposal.expected_result, "verification_method": proposal.verification_method})

    if not isinstance(approval, dict) or approval.get("proposal_id") != proposal_id or type(approval.get("approval_id")) is not int:
        raise ValueError("Approval resume data is invalid")

    return {"approval_id": approval["approval_id"]}


def recheck_action(state: SupportCaseState) -> dict[str, object]:
    proposal_id = state["proposal_id"]
    approval_id = state["approval_id"]
    result = state["investigation_result"]

    if proposal_id is None or approval_id is None or not isinstance(result, SupportInvestigationResult):
        raise RuntimeError("Approval state is incomplete")

    approval, policy = recheck_approval(state["ticket_id"], proposal_id, approval_id, state["evidence"])

    if approval.decision == "reject":
        rejected_result = result.model_copy(update={"outcome": "engineer_escalation", "resolution": None, "escalation_reason": "The proposed action was rejected by the demo approver.", "customer_explanation": "技术人员没有批准这次操作，系统没有进行任何更改。工单已经交给工程师继续检查。"})
        return {"investigation_result": rejected_result, "policy_decision": policy, "executable_action": None}

    if policy.status == "rejected" or policy.action is None:
        rejected_result = result.model_copy(update={"outcome": "engineer_escalation", "resolution": None, "escalation_reason": policy.reason, "customer_explanation": "批准后重新检查时发现当前状态已经不适合执行，系统没有进行任何更改。工单已经交给工程师继续检查。"})
        return {"investigation_result": rejected_result, "policy_decision": policy, "executable_action": None}

    return {"policy_decision": policy, "executable_action": policy.action}


def route_after_approval(state: SupportCaseState) -> str:
    if state["executable_action"] is None:
        return "finalize_support_result"

    return "execute_action"


def execute_action(state: SupportCaseState) -> dict[str, object]:
    proposal_id = state["proposal_id"]
    action = state["executable_action"]

    if proposal_id is None or action is None:
        raise RuntimeError("Approved executable action is missing")

    execution = execute_retry_action(proposal_id, action)
    return {"action_execution": execution}


def verify_action(state: SupportCaseState) -> dict[str, object]:
    proposal_id = state["proposal_id"]
    action = state["executable_action"]
    execution = state["action_execution"]
    result = state["investigation_result"]

    if proposal_id is None or action is None or execution is None or not isinstance(result, SupportInvestigationResult):
        raise RuntimeError("Action verification state is incomplete")

    verified, current_operation, verification_error = verify_retry_action(action, execution)
    save_verification_result(proposal_id, verified, verification_error)

    if verified:
        verified_result = result.model_copy(update={"conclusion": "The approved report export retry completed, and a read-only check confirmed the operation succeeded.", "resolution": "The failed report export was retried after approval and verified as succeeded.", "escalation_reason": None, "customer_explanation": "技术人员已经确认并重新处理了报表导出。最新状态显示处理成功，你现在可以重新下载报表。", "outcome": "resolution"})
        return {"investigation_result": verified_result}

    failed_result = result.model_copy(update={"conclusion": "The approved action did not produce a verified successful operation state.", "resolution": None, "escalation_reason": verification_error or "The operation could not be verified after execution.", "customer_explanation": "操作完成后仍然无法确认报表已经恢复，工单已经交给工程师继续检查。你不需要重复说明之前的信息。", "outcome": "engineer_escalation"})
    return {"investigation_result": failed_result, "error": None, "tool_errors": state["tool_errors"] + ([verification_error] if verification_error is not None else [])}


def finalize_support_result(state: SupportCaseState) -> dict[str, object]:
    result = state["investigation_result"]
    if not isinstance(result, SupportInvestigationResult):
        raise RuntimeError("Support evidence has not been validated")
    if result.outcome == "engineer_escalation":
        package = build_engineer_escalation_package(state["ticket"], result, state["tool_errors"], state["tools_used"])
        result = result.model_copy(update={"escalation_package": package})
    return {"investigation_result": result}


support_workflow_builder = StateGraph(SupportCaseState)
support_workflow_builder.add_node("load_handoff", load_handoff)
support_workflow_builder.add_node("investigate", investigate)
support_workflow_builder.add_node("validate_evidence", validate_evidence)
support_workflow_builder.add_node("evaluate_action", evaluate_action)
support_workflow_builder.add_node("save_action_proposal", save_proposed_action)
support_workflow_builder.add_node("wait_for_approval", wait_for_approval)
support_workflow_builder.add_node("recheck_action", recheck_action)
support_workflow_builder.add_node("execute_action", execute_action)
support_workflow_builder.add_node("verify_action", verify_action)
support_workflow_builder.add_node("finalize_support_result", finalize_support_result)
support_workflow_builder.add_edge(START, "load_handoff")
support_workflow_builder.add_conditional_edges(
    "load_handoff",
    route_after_handoff,
    {"investigate": "investigate", "validate_evidence": "validate_evidence"},
)
support_workflow_builder.add_edge("investigate", "validate_evidence")
support_workflow_builder.add_conditional_edges("validate_evidence", route_after_validation, {"evaluate_action": "evaluate_action", "finalize_support_result": "finalize_support_result"})
support_workflow_builder.add_conditional_edges("evaluate_action", route_after_policy, {"save_action_proposal": "save_action_proposal", "finalize_support_result": "finalize_support_result"})
support_workflow_builder.add_edge("save_action_proposal", "wait_for_approval")
support_workflow_builder.add_edge("wait_for_approval", "recheck_action")
support_workflow_builder.add_conditional_edges("recheck_action", route_after_approval, {"execute_action": "execute_action", "finalize_support_result": "finalize_support_result"})
support_workflow_builder.add_edge("execute_action", "verify_action")
support_workflow_builder.add_edge("verify_action", "finalize_support_result")
support_workflow_builder.add_edge("finalize_support_result", END)

def build_support_investigation_graph(checkpointer):
    return support_workflow_builder.compile(checkpointer=checkpointer)


def load_saved_support_result(ticket_id: int) -> SupportInvestigationResponse | None:
    with SessionLocal() as database:
        ticket = database.get(Ticket, ticket_id)

        if ticket is None:
            raise ValueError("Ticket not found")

        if ticket.investigation_result is None:
            return None

        result = SupportInvestigationResult.model_validate(ticket.investigation_result)
        proposal, _, _ = get_ticket_action_records(ticket_id)
        return SupportInvestigationResponse(ticket_id=ticket_id, result=result, tools_used=ticket.investigation_tools or [], status=TicketStatus(ticket.status), proposal_id=proposal.id if proposal is not None else None)


def save_waiting_for_approval(ticket_id: int, investigation_result: SupportInvestigationResult, tools_used: list[str]) -> None:
    with SessionLocal() as database:
        ticket = database.get(Ticket, ticket_id)

        if ticket is None:
            raise ValueError("Ticket not found")

        ticket.investigation_result = investigation_result.model_dump(mode="json")
        ticket.investigation_tools = tools_used
        ticket.status = TicketStatus.AWAITING_APPROVAL.value

        if ticket.support_session_id is not None:
            support_session = database.get(SupportSession, ticket.support_session_id)

            if support_session is None:
                raise ValueError("Support session not found")

            support_session.status = "waiting_for_approval"
            support_session.customer_result = investigation_result.customer_explanation

        database.commit()


def save_support_result(ticket_id: int, investigation_result: SupportInvestigationResult, tools_used: list[str]) -> None:
    with SessionLocal() as database:
        ticket = database.get(Ticket, ticket_id)

        if ticket is None:
            raise ValueError("Ticket not found")

        if ticket.investigation_result is not None and ticket.status in (TicketStatus.RESOLVED.value, TicketStatus.ENGINEER_ESCALATION.value):
            return

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
    saved_result = load_saved_support_result(ticket_id)

    if saved_result is not None:
        return saved_result

    initial_state: SupportCaseState = {
        "ticket_id": ticket_id,
        "ticket": None,
        "handoff": None,
        "investigation_result": None,
        "evidence": [],
        "tool_errors": [],
        "tools_used": [],
        "action_proposal": None,
        "policy_decision": None,
        "proposal_id": None,
        "approval_id": None,
        "executable_action": None,
        "action_execution": None,
        "error": None,
    }

    config = {"configurable": {"thread_id": f"support-ticket-{ticket_id}"}, "recursion_limit": 20}

    with checkpointing.open_postgres_checkpointer() as checkpointer:
        support_graph = build_support_investigation_graph(checkpointer)
        saved_state = support_graph.get_state(config)

        if len(saved_state.values) == 0:
            final_state = support_graph.invoke(initial_state, config)
        elif len(saved_state.next) > 0:
            final_state = support_graph.invoke(None, config)
        else:
            final_state = saved_state.values

        saved_state = support_graph.get_state(config)

    investigation_result = final_state["investigation_result"]

    if investigation_result is None:
        raise RuntimeError("Support workflow did not produce a result")

    if isinstance(investigation_result, SupportInvestigationResult) is False:
        investigation_result = SupportInvestigationResult.model_validate(investigation_result)

    tools_used = final_state["tools_used"]

    if len(saved_state.interrupts) > 0:
        proposal_id = final_state["proposal_id"]

        if proposal_id is None:
            raise RuntimeError("Interrupted support workflow has no action proposal")

        save_waiting_for_approval(ticket_id, investigation_result, tools_used)
        return SupportInvestigationResponse(ticket_id=ticket_id, result=investigation_result, tools_used=tools_used, status=TicketStatus.AWAITING_APPROVAL, proposal_id=proposal_id)

    save_support_result(ticket_id, investigation_result, tools_used)

    return load_saved_support_result(ticket_id) or SupportInvestigationResponse(ticket_id=ticket_id, result=investigation_result, tools_used=tools_used)


def resume_support_investigation(ticket_id: int, proposal_id: int, approval_id: int) -> SupportInvestigationResponse:
    saved_result = load_saved_support_result(ticket_id)

    if saved_result is not None and saved_result.status in (TicketStatus.RESOLVED, TicketStatus.ENGINEER_ESCALATION):
        return saved_result

    config = {"configurable": {"thread_id": f"support-ticket-{ticket_id}"}, "recursion_limit": 20}

    with checkpointing.open_postgres_checkpointer() as checkpointer:
        support_graph = build_support_investigation_graph(checkpointer)
        saved_state = support_graph.get_state(config)

        if len(saved_state.values) == 0:
            raise RuntimeError("Support investigation state not found")

        if len(saved_state.interrupts) > 0:
            final_state = support_graph.invoke(Command(resume={"proposal_id": proposal_id, "approval_id": approval_id}), config)
        elif len(saved_state.next) > 0:
            final_state = support_graph.invoke(None, config)
        else:
            final_state = saved_state.values

        saved_state = support_graph.get_state(config)

    if len(saved_state.interrupts) > 0:
        raise RuntimeError("Support investigation is still awaiting approval")

    investigation_result = final_state["investigation_result"]

    if not isinstance(investigation_result, SupportInvestigationResult):
        investigation_result = SupportInvestigationResult.model_validate(investigation_result)

    save_support_result(ticket_id, investigation_result, final_state["tools_used"])
    return load_saved_support_result(ticket_id) or SupportInvestigationResponse(ticket_id=ticket_id, result=investigation_result, tools_used=final_state["tools_used"])
