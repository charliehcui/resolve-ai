import argparse
import json
import os
import sys
from pathlib import Path

from langsmith import traceable

from backend.app import support_action_plans
from backend.app.auth import authenticate
from backend.app.config import PROJECT_ROOT, get_settings
from backend.app.conversations import process_conversation_message
from backend.app.customer_document_ingestion import generate_text_embeddings, import_product_documents
from backend.app.database import database_is_ready, initialize_database
from backend.app.models import DoctorPlatformStatusRequest, DoctorShopStatusRequest, DoctorStatusResult, create_model
from backend.app.report import export_ticket_html
from backend.app.support_action_approvals import decide_action_plan, execute_action_plan, get_action_details
from backend.app.support_action_registry import ACTION_ALIASES, ACTION_REGISTRY, action_policy
from backend.app.support_cases import show_case
from backend.app.tickets import create_ticket, list_engineer_tickets, recheck_ticket, show_ticket
from backend.app.trace import current_trace_id, wait_for_langsmith_run


@traceable(name="phase_1_doctor", run_type="chain")
def run_doctor_checks() -> dict[str, object]:
    settings = get_settings()
    if not database_is_ready():
        raise RuntimeError("PostgreSQL is not ready")
    vector = generate_text_embeddings(["ResolveAI Phase 1 doctor"], "RETRIEVAL_QUERY")[0]
    model = create_model()
    structured_result = model.with_structured_output(DoctorStatusResult).invoke("Return status ok.")
    tool_result = model.bind_tools([DoctorShopStatusRequest, DoctorPlatformStatusRequest]).invoke("In one response, call DoctorShopStatusRequest with shop_id shop-a and DoctorPlatformStatusRequest with platform_id platform-a. Both are independent read-only checks; call both.")
    if structured_result.status != "ok":
        raise RuntimeError("Structured output capability check failed")
    tool_names = {call["name"] for call in tool_result.tool_calls}
    if tool_names != {"DoctorShopStatusRequest", "DoctorPlatformStatusRequest"}:
        raise RuntimeError("OpenRouter parallel tool calling capability check failed")
    return {
        "database": "ok",
        "embedding_dimension": len(vector),
        "openrouter_structured_output": "ok",
        "openrouter_tool_calling": "ok",
        "openrouter_parallel_tool_call_count": len(tool_result.tool_calls),
        "langsmith_tracing": settings.langsmith_tracing,
        "trace_id": current_trace_id(),
    }


def doctor_command() -> None:
    result = run_doctor_checks()
    result["langsmith_run_available"] = wait_for_langsmith_run(result.get("trace_id")) if result["langsmith_tracing"] else False
    print(json.dumps(result, ensure_ascii=False, indent=2))


def chat_command(token: str, question: str, conversation_id: str | None, retrieval_mode: str | None) -> None:
    user = authenticate(token)
    result = process_conversation_message(user, question, conversation_id, retrieval_mode)
    print(f"Active role: {result['active_role']}")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def case_show_command(token: str, case_id: str) -> None:
    user = authenticate(token)
    print(json.dumps(show_case(case_id, user), ensure_ascii=False, indent=2, default=str))


def action_plan_command(token: str, case_id: str, action_type: str, enable_order_sync: bool, enable_shipment_sync: bool) -> None:
    user = authenticate(token)
    action_type = ACTION_ALIASES.get(action_type, action_type)
    builder = getattr(support_action_plans, action_policy({"action_type": action_type})["builder"])
    if action_type == "resend_shipment":
        result = builder(user, case_id, enable_shipment_sync)
    elif action_type in {"retry_order_sync", "retry_failed_task"}:
        result = builder(user, case_id, enable_order_sync, action_type)
    else:
        result = builder(user, case_id)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def action_decide_command(token: str, action_id: str, decision: str) -> None:
    user = authenticate(token)
    print(json.dumps(decide_action_plan(user, action_id, decision), ensure_ascii=False, indent=2, default=str))


def action_execute_command(token: str, action_id: str) -> None:
    user = authenticate(token)
    print(json.dumps(execute_action_plan(user, action_id), ensure_ascii=False, indent=2, default=str))


def action_show_command(token: str, action_id: str) -> None:
    user = authenticate(token)
    print(json.dumps(get_action_details(user, action_id), ensure_ascii=False, indent=2, default=str))


def ticket_create_command(token: str, conversation_id: str, reason: str) -> None:
    print(json.dumps(create_ticket(authenticate(token), conversation_id, "user_requested", reason), ensure_ascii=False, indent=2, default=str))


def ticket_show_command(token: str, ticket_id: str) -> None:
    print(json.dumps(show_ticket(authenticate(token), ticket_id), ensure_ascii=False, indent=2, default=str))


def ticket_list_command(token: str) -> None:
    print(json.dumps(list_engineer_tickets(authenticate(token)), ensure_ascii=False, indent=2, default=str))


def ticket_recheck_command(token: str, ticket_id: str) -> None:
    print(json.dumps(recheck_ticket(authenticate(token), ticket_id), ensure_ascii=False, indent=2, default=str))


def ticket_export_command(token: str, ticket_id: str, output: str | None) -> None:
    output_path = Path(output) if output else PROJECT_ROOT / "reports" / f"ticket-{ticket_id}.html"
    result = export_ticket_html(authenticate(token), ticket_id, output_path.resolve())
    print(json.dumps({"ticket_id": ticket_id, "output": str(result)}, ensure_ascii=False, indent=2))


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
    action_plan = action_commands.add_parser("plan", aliases=["propose"])
    action_plan.add_argument("case_id")
    action_plan.add_argument("--type", choices=[*ACTION_REGISTRY, *ACTION_ALIASES], default="retry_order_sync")
    action_plan.add_argument("--enable-order-sync", action="store_true")
    action_plan.add_argument("--enable-shipment-sync", action="store_true")
    action_plan.add_argument("--token", default=os.getenv("RESOLVEAI_TOKEN"))
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
    ticket = commands.add_parser("ticket")
    ticket_commands = ticket.add_subparsers(dest="ticket_command", required=True)
    ticket_create = ticket_commands.add_parser("create")
    ticket_create.add_argument("conversation_id")
    ticket_create.add_argument("--reason", default="User requested engineer support")
    ticket_create.add_argument("--token", default=os.getenv("RESOLVEAI_TOKEN"))
    ticket_show = ticket_commands.add_parser("show")
    ticket_show.add_argument("ticket_id")
    ticket_show.add_argument("--token", default=os.getenv("RESOLVEAI_TOKEN"))
    ticket_list = ticket_commands.add_parser("list")
    ticket_list.add_argument("--token", default=os.getenv("RESOLVEAI_TOKEN"))
    ticket_recheck = ticket_commands.add_parser("recheck")
    ticket_recheck.add_argument("ticket_id")
    ticket_recheck.add_argument("--token", default=os.getenv("RESOLVEAI_TOKEN"))
    ticket_export = ticket_commands.add_parser("export")
    ticket_export.add_argument("ticket_id")
    ticket_export.add_argument("--output")
    ticket_export.add_argument("--token", default=os.getenv("RESOLVEAI_TOKEN"))
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
            print(json.dumps(import_product_documents(), ensure_ascii=False))
        elif args.command == "doctor":
            doctor_command()
        elif args.command == "chat":
            chat_command(args.token or "", args.question, args.conversation, args.mode)
        elif args.command == "case" and args.case_command == "show":
            case_show_command(args.token or "", args.case_id)
        elif args.command == "action" and args.action_command in {"plan", "propose"}:
            action_plan_command(args.token or "", args.case_id, args.type, args.enable_order_sync, args.enable_shipment_sync)
        elif args.command == "action" and args.action_command == "decide":
            action_decide_command(args.token or "", args.action_id, args.decision)
        elif args.command == "action" and args.action_command == "execute":
            action_execute_command(args.token or "", args.action_id)
        elif args.command == "action" and args.action_command == "show":
            action_show_command(args.token or "", args.action_id)
        elif args.command == "ticket" and args.ticket_command == "create":
            ticket_create_command(args.token or "", args.conversation_id, args.reason)
        elif args.command == "ticket" and args.ticket_command == "show":
            ticket_show_command(args.token or "", args.ticket_id)
        elif args.command == "ticket" and args.ticket_command == "list":
            ticket_list_command(args.token or "")
        elif args.command == "ticket" and args.ticket_command == "recheck":
            ticket_recheck_command(args.token or "", args.ticket_id)
        elif args.command == "ticket" and args.ticket_command == "export":
            ticket_export_command(args.token or "", args.ticket_id, args.output)
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
