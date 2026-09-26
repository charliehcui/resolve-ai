import time

from backend.app.auth import authorize_conversation
from backend.app.config import get_settings
from backend.app.customer_workflow import run_customer_workflow
from backend.app.database import create_conversation, load_messages, save_agent_run, save_message
from backend.app.handoff import create_support_handoff
from backend.app.models import UserContext, provider_for_task
from backend.app.support_tools import create_ticket
from backend.app.support_workflow import run_support_workflow
from backend.app.tickets import is_human_request
from backend.app.trace import current_trace_id


def process_conversation_message(user: UserContext, question: str, conversation_id: str | None = None, retrieval_mode: str | None = None) -> dict[str, object]:
    """Run one conversation turn for every CLI/API/UI entry point."""
    settings = get_settings()
    if user.role == "engineer":
        raise PermissionError("Engineer identities cannot use merchant conversations")
    if conversation_id:
        conversation = authorize_conversation(user, conversation_id)
    else:
        conversation_id = create_conversation(user.company_id, user.user_id)
        conversation = authorize_conversation(user, conversation_id)
    history = load_messages(conversation_id)
    save_message(conversation_id, "user", question)
    started = time.perf_counter()

    if is_human_request(question):
        ticket = create_ticket(user, conversation_id, "user_requested", question)
        answer = "Your request has been sent to the assigned engineer queue."
        save_message(conversation_id, "assistant", answer, {"agent_role": conversation["active_role"], "ticket_id": ticket["ticket_id"], "status": "pending_human"})
        run_id = save_agent_run(conversation_id, "code", "none", "engineer_handoff", "succeeded", int((time.perf_counter() - started) * 1000), {}, current_trace_id())
        return {"conversation_id": conversation_id, "run_id": run_id, "active_role": conversation["active_role"], "answer": answer, "ticket_id": ticket["ticket_id"], "status": "pending_human", "evidence_ids": [], "usage": {}}

    if conversation["active_role"] == "SUPPORT":
        try:
            result = run_support_workflow(question, user, conversation_id)
            latency_ms = int((time.perf_counter() - started) * 1000)
            metadata = {"agent_role": "SUPPORT", "case_id": result.case_id, "status": result.status, "evidence_ids": result.evidence_ids, "tool_path": result.tool_path, "ticket_id": result.ticket_id}
            save_message(conversation_id, "assistant", result.answer, metadata)
            provider = provider_for_task("support_investigation") if len(result.usage) > 0 else "code"
            model_name = settings.google_model if len(result.usage) > 0 else "none"
            run_id = save_agent_run(conversation_id, provider, model_name, "support_investigation", "succeeded", latency_ms, result.usage, result.trace_id)
        except Exception as error:
            save_agent_run(conversation_id, provider_for_task("support_investigation"), settings.google_model, "support_investigation", "failed", int((time.perf_counter() - started) * 1000), {}, current_trace_id(), type(error).__name__)
            raise
        return {"conversation_id": conversation_id, "run_id": run_id, "active_role": "SUPPORT", **result.model_dump()}

    try:
        answer, trace_id = run_customer_workflow(question, user, conversation_id, history, retrieval_mode or settings.retrieval_mode)
        case_id = None
        if answer.needs_support:
            _, case_id = create_support_handoff(user, conversation_id, question, history)
        metadata = {"agent_role": "CUSTOMER", "citations": [citation.model_dump() for citation in answer.citations], "claims": [claim.model_dump() for claim in answer.claims], "removed_claims": answer.removed_claims, "needs_support": answer.needs_support, "case_id": case_id, "retrieval_mode": retrieval_mode or settings.retrieval_mode}
        save_message(conversation_id, "assistant", answer.answer, metadata)
        run_id = save_agent_run(conversation_id, "groq", settings.groq_model, "customer_rag_answer", "succeeded", int((time.perf_counter() - started) * 1000), answer.usage, trace_id)
    except Exception as error:
        save_agent_run(conversation_id, provider_for_task("customer_answer"), settings.groq_model, "customer_rag_answer", "failed", int((time.perf_counter() - started) * 1000), {}, current_trace_id(), type(error).__name__)
        raise
    return {"conversation_id": conversation_id, "run_id": run_id, "active_role": "SUPPORT" if answer.needs_support else "CUSTOMER", "answer": answer.answer, "citations": [citation.model_dump() for citation in answer.citations], "needs_support": answer.needs_support, "case_id": case_id, "usage": answer.usage}
