import time

from backend.app.auth import authorize_conversation
from backend.app.config import get_settings
from backend.app.customer_workflow import run_customer_graph
from backend.app.database import create_conversation, load_messages, save_agent_run, save_message
from backend.app.handoff import handoff_to_support
from backend.app.models import AuthContext, provider_for_task
from backend.app.support_tools import create_ticket
from backend.app.support_workflow import run_support_graph
from backend.app.tickets import is_human_request
from backend.app.trace import current_trace_id


def process_conversation_message(auth: AuthContext, question: str, conversation_id: str | None = None, retrieval_mode: str | None = None) -> dict[str, object]:
    """Run one conversation turn for every CLI/API/UI entry point."""
    settings = get_settings()
    if auth.role == "engineer":
        raise PermissionError("Engineer identities cannot use merchant conversations")
    if conversation_id:
        conversation = authorize_conversation(auth, conversation_id)
    else:
        conversation_id = create_conversation(auth.company_id, auth.user_id)
        conversation = authorize_conversation(auth, conversation_id)
    history = load_messages(conversation_id)
    save_message(conversation_id, "user", question)
    started = time.perf_counter()

    if is_human_request(question):
        ticket = create_ticket(auth, conversation_id, "user_requested", question)
        answer = "Your request has been sent to the assigned engineer queue."
        save_message(conversation_id, "assistant", answer, {"agent_role": conversation["active_role"], "ticket_id": ticket["ticket_id"], "status": "pending_human"})
        run_id = save_agent_run(conversation_id, "code", "none", "engineer_handoff", "succeeded", int((time.perf_counter() - started) * 1000), {}, current_trace_id())
        return {"conversation_id": conversation_id, "run_id": run_id, "active_role": conversation["active_role"], "answer": answer, "ticket_id": ticket["ticket_id"], "status": "pending_human", "evidence_ids": [], "models_used": [], "usage": {}}

    if conversation["active_role"] == "SUPPORT":
        try:
            result = run_support_graph(question, auth, conversation_id)
            latency_ms = int((time.perf_counter() - started) * 1000)
            metadata = {"agent_role": "SUPPORT", "case_id": result.case_id, "status": result.status, "evidence_ids": result.evidence_ids, "tool_path": result.tool_path, "models_used": result.models_used, "ticket_id": result.ticket_id}
            save_message(conversation_id, "assistant", result.answer, metadata)
            provider = provider_for_task("support_investigation") if result.models_used else "code"
            model_name = " -> ".join(dict.fromkeys(result.models_used)) if result.models_used else "none"
            run_id = save_agent_run(conversation_id, provider, model_name, "support_investigation", "succeeded", latency_ms, result.usage, result.trace_id)
        except Exception as error:
            attempted_models = getattr(error, "attempted_models", [settings.google_model])
            save_agent_run(conversation_id, provider_for_task("support_investigation"), " -> ".join(dict.fromkeys(attempted_models)), "support_investigation", "failed", int((time.perf_counter() - started) * 1000), {}, current_trace_id(), type(error).__name__)
            raise
        return {"conversation_id": conversation_id, "run_id": run_id, "active_role": "SUPPORT", **result.model_dump()}

    try:
        answer, trace_id = run_customer_graph(question, auth, conversation_id, history, retrieval_mode or settings.retrieval_mode)
        case_id = None
        if answer.needs_support:
            _, case_id = handoff_to_support(auth, conversation_id, question, answer.answer, history)
        metadata = {"agent_role": "CUSTOMER", "citations": [citation.model_dump() for citation in answer.citations], "claims": [claim.model_dump() for claim in answer.claims], "removed_claims": answer.removed_claims, "needs_support": answer.needs_support, "case_id": case_id, "retrieval_mode": retrieval_mode or settings.retrieval_mode}
        save_message(conversation_id, "assistant", answer.answer, metadata)
        run_id = save_agent_run(conversation_id, "groq", settings.groq_model, "customer_rag_answer", "succeeded", int((time.perf_counter() - started) * 1000), answer.usage, trace_id)
    except Exception as error:
        save_agent_run(conversation_id, provider_for_task("customer_answer"), settings.groq_model, "customer_rag_answer", "failed", int((time.perf_counter() - started) * 1000), {}, current_trace_id(), type(error).__name__)
        raise
    return {"conversation_id": conversation_id, "run_id": run_id, "active_role": "SUPPORT" if answer.needs_support else "CUSTOMER", "answer": answer.answer, "citations": [citation.model_dump() for citation in answer.citations], "needs_support": answer.needs_support, "case_id": case_id, "models_used": [settings.groq_model], "usage": answer.usage}
