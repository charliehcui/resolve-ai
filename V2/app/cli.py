import argparse
import json
import os
import sys
import time

from langsmith import traceable

from app.actions import decide_action, execute_order_recovery, propose_order_recovery, show_action
from app.auth import authenticate, authorize_conversation
from app.config import get_settings
from app.db import create_conversation, database_is_ready, initialize_database, load_messages, save_agent_run, save_message
from app.docs import embed_texts, import_product_docs
from app.graph import run_customer_graph, run_support_graph
from app.models import DoctorPlatformLookup, DoctorShopLookup, DoctorStructuredResult, create_google_model, create_groq_model, provider_for_task
from app.support import handoff_to_support, show_case
from app.trace import current_trace_id, wait_for_langsmith_run


@traceable(name="phase_1_doctor", run_type="chain")
def run_doctor_checks() -> dict[str, object]:
    settings = get_settings()
    if not database_is_ready():
        raise RuntimeError("PostgreSQL is not ready")
    vector = embed_texts(["ResolveAI Phase 1 doctor"], "RETRIEVAL_QUERY")[0]
    groq_result = create_groq_model().with_structured_output(DoctorStructuredResult).invoke("Return status ok.")
    google_model = create_google_model()
    google_result = google_model.with_structured_output(DoctorStructuredResult).invoke("Return status ok.")
    tool_result = google_model.bind_tools([DoctorShopLookup, DoctorPlatformLookup], tool_choice="any").invoke("In one response, call DoctorShopLookup with shop_id shop-a and DoctorPlatformLookup with platform_id platform-a. Both are independent read-only checks; call both.")
    if groq_result.status != "ok" or google_result.status != "ok":
        raise RuntimeError("Structured output capability check failed")
    tool_names = {call["name"] for call in tool_result.tool_calls}
    if tool_names != {"DoctorShopLookup", "DoctorPlatformLookup"}:
        raise RuntimeError("Google parallel tool calling capability check failed")
    return {
        "database": "ok",
        "embedding_dimension": len(vector),
        "groq_structured_output": "ok",
        "google_structured_output": "ok",
        "google_tool_calling": "ok",
        "google_parallel_tool_calling": "ok",
        "google_parallel_tool_call_count": len(tool_result.tool_calls),
        "langsmith_tracing": settings.langsmith_tracing,
        "trace_id": current_trace_id(),
    }


def doctor_command() -> None:
    result = run_doctor_checks()
    result["langsmith_run_available"] = wait_for_langsmith_run(result.get("trace_id")) if result["langsmith_tracing"] else False
    print(json.dumps(result, ensure_ascii=False, indent=2))


def chat_command(token: str, question: str, conversation_id: str | None, retrieval_mode: str | None) -> None:
    settings = get_settings()
    auth = authenticate(token)
    if conversation_id:
        conversation = authorize_conversation(auth, conversation_id)
    else:
        conversation_id = create_conversation(auth.company_id, auth.user_id)
        conversation = authorize_conversation(auth, conversation_id)
    history = load_messages(conversation_id)
    save_message(conversation_id, "user", question)
    started = time.perf_counter()
    if conversation["active_role"] == "SUPPORT":
        try:
            result = run_support_graph(question, auth, conversation_id)
            latency_ms = int((time.perf_counter() - started) * 1000)
            save_message(conversation_id, "assistant", result.answer, {"agent_role": "SUPPORT", "case_id": result.case_id, "status": result.status, "evidence_ids": result.evidence_ids, "tool_path": result.tool_path, "models_used": result.models_used})
            provider = provider_for_task("support_investigation") if result.models_used else "code"
            model_name = " -> ".join(dict.fromkeys(result.models_used)) if result.models_used else "none"
            run_id = save_agent_run(conversation_id, provider, model_name, "support_investigation", "succeeded", latency_ms, result.usage, result.trace_id)
        except Exception as error:
            latency_ms = int((time.perf_counter() - started) * 1000)
            attempted_models = getattr(error, "attempted_models", [settings.google_model])
            save_agent_run(conversation_id, provider_for_task("support_investigation"), " -> ".join(dict.fromkeys(attempted_models)), "support_investigation", "failed", latency_ms, {}, current_trace_id(), type(error).__name__)
            raise
        print(f"Conversation: {conversation_id}")
        print(f"Case: {result.case_id}")
        print(f"Run: {run_id}")
        print("Active role: SUPPORT")
        print(f"Answer: {result.answer}")
        print(f"Evidence: {', '.join(result.evidence_ids) or 'none'}")
        print(f"Tool path: {', '.join(result.tool_path) or 'none'}")
        print(f"Models: {', '.join(result.models_used) or 'none'}")
        print(f"Usage: {result.usage.get('total_tokens') if result.usage.get('total_tokens') is not None else 'unknown'}")
        return
    try:
        answer, trace_id = run_customer_graph(question, auth, conversation_id, history, retrieval_mode or settings.retrieval_mode)
        latency_ms = int((time.perf_counter() - started) * 1000)
        case_id = None
        if answer.needs_support:
            _, case_id = handoff_to_support(auth, conversation_id, question, answer.answer, history)
        save_message(conversation_id, "assistant", answer.answer, {"agent_role": "CUSTOMER", "citations": [citation.model_dump() for citation in answer.citations], "claims": [claim.model_dump() for claim in answer.claims], "removed_claims": answer.removed_claims, "needs_support": answer.needs_support, "case_id": case_id, "retrieval_mode": retrieval_mode or settings.retrieval_mode})
        run_id = save_agent_run(conversation_id, "groq", settings.groq_model, "customer_rag_answer", "succeeded", latency_ms, answer.usage, trace_id)
    except Exception as error:
        latency_ms = int((time.perf_counter() - started) * 1000)
        save_agent_run(conversation_id, provider_for_task("customer_answer"), settings.groq_model, "customer_rag_answer", "failed", latency_ms, {}, current_trace_id(), type(error).__name__)
        raise
    print(f"Conversation: {conversation_id}")
    print(f"Run: {run_id}")
    print(f"Answer: {answer.answer}")
    print("Sources:")
    for citation in answer.citations:
        print(f"- {citation.title} | {citation.chunk_id} | {citation.source_uri}")
    if answer.needs_support:
        print(f"Handoff: active role changed to SUPPORT; case {case_id}. Customer Agent read no backend data.")
    if answer.usage.get("total_tokens") is None:
        print("Usage: unknown")
    else:
        print(f"Usage: {answer.usage['total_tokens']} tokens")


def case_show_command(token: str, case_id: str) -> None:
    auth = authenticate(token)
    print(json.dumps(show_case(case_id, auth), ensure_ascii=False, indent=2, default=str))


def action_propose_command(token: str, case_id: str, enable_order_sync: bool) -> None:
    auth = authenticate(token)
    print(json.dumps(propose_order_recovery(auth, case_id, enable_order_sync), ensure_ascii=False, indent=2, default=str))


def action_decide_command(token: str, action_id: str, decision: str) -> None:
    auth = authenticate(token)
    print(json.dumps(decide_action(auth, action_id, decision), ensure_ascii=False, indent=2, default=str))


def action_execute_command(token: str, action_id: str) -> None:
    auth = authenticate(token)
    print(json.dumps(execute_order_recovery(auth, action_id), ensure_ascii=False, indent=2, default=str))


def action_show_command(token: str, action_id: str) -> None:
    auth = authenticate(token)
    print(json.dumps(show_action(auth, action_id), ensure_ascii=False, indent=2, default=str))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="resolveai")
    commands = parser.add_subparsers(dest="command", required=True)
    database = commands.add_parser("db")
    database_commands = database.add_subparsers(dest="database_command", required=True)
    database_commands.add_parser("init")
    docs = commands.add_parser("docs")
    docs_commands = docs.add_subparsers(dest="docs_command", required=True)
    docs_commands.add_parser("import")
    commands.add_parser("doctor")
    chat = commands.add_parser("chat")
    chat.add_argument("question")
    chat.add_argument("--token", default=os.getenv("RESOLVEAI_TOKEN"))
    chat.add_argument("--conversation")
    chat.add_argument("--mode", choices=["vector_only", "hybrid", "hybrid_rerank"])
    case = commands.add_parser("case")
    case_commands = case.add_subparsers(dest="case_command", required=True)
    case_show = case_commands.add_parser("show")
    case_show.add_argument("case_id")
    case_show.add_argument("--token", default=os.getenv("RESOLVEAI_TOKEN"))
    action = commands.add_parser("action")
    action_commands = action.add_subparsers(dest="action_command", required=True)
    action_propose = action_commands.add_parser("propose")
    action_propose.add_argument("case_id")
    action_propose.add_argument("--enable-order-sync", action="store_true")
    action_propose.add_argument("--token", default=os.getenv("RESOLVEAI_TOKEN"))
    action_decide = action_commands.add_parser("decide")
    action_decide.add_argument("action_id")
    action_decide.add_argument("--decision", choices=["approve", "reject"], required=True)
    action_decide.add_argument("--token", default=os.getenv("RESOLVEAI_TOKEN"))
    action_execute = action_commands.add_parser("execute")
    action_execute.add_argument("action_id")
    action_execute.add_argument("--token", default=os.getenv("RESOLVEAI_TOKEN"))
    action_show = action_commands.add_parser("show")
    action_show.add_argument("action_id")
    action_show.add_argument("--token", default=os.getenv("RESOLVEAI_TOKEN"))
    return parser


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    try:
        if args.command == "db" and args.database_command == "init":
            initialize_database()
            print("Database migrations applied.")
        elif args.command == "docs" and args.docs_command == "import":
            print(json.dumps(import_product_docs(), ensure_ascii=False))
        elif args.command == "doctor":
            doctor_command()
        elif args.command == "chat":
            chat_command(args.token or "", args.question, args.conversation, args.mode)
        elif args.command == "case" and args.case_command == "show":
            case_show_command(args.token or "", args.case_id)
        elif args.command == "action" and args.action_command == "propose":
            action_propose_command(args.token or "", args.case_id, args.enable_order_sync)
        elif args.command == "action" and args.action_command == "decide":
            action_decide_command(args.token or "", args.action_id, args.decision)
        elif args.command == "action" and args.action_command == "execute":
            action_execute_command(args.token or "", args.action_id)
        elif args.command == "action" and args.action_command == "show":
            action_show_command(args.token or "", args.action_id)
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
