import html
import json
from pathlib import Path
from typing import Any

from backend.app.models import UserContext

USAGE_KEYS = ("groq_calls", "google_calls", "embedding_calls", "groq_input", "groq_output", "google_input", "google_output", "embedding_input")


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [item for item in results if item["check"]["status"] != "not_scored"]
    usage = {key: sum(float(item.get("usage", {}).get(key, 0)) for item in results) for key in USAGE_KEYS}
    return {
        "runs": len(results),
        "scored_runs": len(scored),
        "passed": sum(item["check"]["status"] == "passed" for item in scored),
        "failed": sum(item["check"]["status"] == "failed" for item in scored),
        "errors": sum(item["status"] == "error" for item in results),
        "not_scored": sum(item["check"]["status"] == "not_scored" for item in results),
        "average_latency_ms": round(sum(item.get("latency_ms", 0) for item in results) / len(results), 2) if results else 0,
        "usage": usage,
    }


def estimate_cost(tokens: dict[str, float], prices: dict[str, Any]) -> dict[str, Any]:
    groq = prices["groq"]
    google = prices["google"]
    embedding = prices["embedding"]
    embedding_rate = embedding.get("input_per_million_tokens")
    embedding_proxy = embedding_rate if embedding_rate is not None else embedding.get("estimate_proxy_per_million_tokens")
    known_cost = (tokens["groq_input"] * groq["input_per_million_tokens"] + tokens["groq_output"] * groq["output_per_million_tokens"] + tokens["google_input"] * google["input_per_million_tokens"] + tokens["google_output"] * google["output_per_million_tokens"]) / 1_000_000
    embedding_cost = None if embedding_proxy is None else tokens["embedding_input"] * embedding_proxy / 1_000_000
    return {
        "known_chat_cost_usd": round(known_cost, 6),
        "embedding_cost_usd": round(embedding_cost, 6) if embedding_cost is not None else None,
        "total_estimate_usd": round(known_cost + embedding_cost, 6) if embedding_cost is not None else None,
        "embedding_price_is_proxy": embedding_rate is None and embedding_proxy is not None,
    }


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
