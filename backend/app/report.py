import html
import json
from pathlib import Path

from backend.app.models import UserContext


def render_ticket_html(ticket: dict[str, object]) -> str:
    fields = {
        "ticket_id": ticket["ticket_id"],
        "status": ticket["status"],
        "category": ticket["category"],
        "customer_problem": ticket["customer_problem"],
        "business_target": ticket["business_target"],
        "confirmed_facts": ticket["confirmed_facts"],
        "possible_causes": ticket["possible_causes"],
        "excluded_causes": ticket["excluded_causes"],
        "unknowns": ticket["unknowns"],
        "next_steps": ticket["next_steps"],
        "current_result": ticket["current_result"],
        "rechecks": ticket.get("rechecks", []),
        "timeline": ticket.get("timeline", []),
    }
    content = html.escape(json.dumps(fields, ensure_ascii=False, indent=2, default=str))
    title = html.escape(f"ResolveAI Ticket {ticket['ticket_id']}")
    return f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{title}</title><style>body{{font-family:system-ui,sans-serif;max-width:960px;margin:40px auto;padding:0 20px;color:#17211c}}pre{{white-space:pre-wrap;background:#f3f6f4;padding:20px;border-radius:10px}}h1{{color:#173d31}}</style></head><body><h1>{title}</h1><p>Exported from deterministic Ticket state. Secrets and access tokens are not included.</p><pre>{content}</pre></body></html>"


def export_ticket_html(user: UserContext, ticket_id: str, output_path: Path) -> Path:
    from backend.app.tickets import show_ticket

    ticket = show_ticket(user, ticket_id)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_ticket_html(ticket), encoding="utf-8")
    return output_path
