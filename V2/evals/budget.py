"""Persistent cross-run accounting. Reserve before HTTP; ambiguous calls keep their reserve."""
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

from backend.app.config import PROJECT_ROOT, get_settings
from backend.app.llm_context import call_context


class BudgetExceeded(RuntimeError):
    pass


def ledger_path() -> Path:
    return Path(os.getenv("EVAL_COST_LEDGER", str(PROJECT_ROOT / ".local" / "eval" / "costs.sqlite3")))


def connection():
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(path, timeout=30)
    database.row_factory = sqlite3.Row
    database.execute("CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, started TEXT, run_id TEXT, case_id TEXT, category TEXT, scope TEXT, model TEXT, provider TEXT, status TEXT, input_tokens INTEGER, output_tokens INTEGER, total_tokens INTEGER, actual_cost REAL, estimated_cost REAL, reserved_cost REAL, charged_cost REAL, generation_id TEXT, error_type TEXT)")
    database.execute("BEGIN IMMEDIATE")
    columns = {row[1] for row in database.execute("PRAGMA table_info(calls)")}
    additions = {"routing_tag": "TEXT", "prompt_price": "REAL", "completion_price": "REAL", "logical_call_id": "TEXT", "primary_model": "TEXT", "fallback_model": "TEXT", "fallback_reason": "TEXT", "fallback_count": "INTEGER DEFAULT 0"}
    for name, kind in additions.items():
        if name not in columns:
            database.execute(f"ALTER TABLE calls ADD COLUMN {name} {kind}")
    database.commit()
    return database


def limits() -> tuple[float, float]:
    budget = float(os.getenv("EVAL_BUDGET_USD", "1.00"))
    stop = float(os.getenv("EVAL_STOP_USD", "0.90"))
    if not 0 < budget <= 1 or not 0 < stop <= budget * 0.95:
        raise ValueError("Budget must be <= $1, with a stop threshold <= 95% of budget")
    return budget, stop


def refresh_prices() -> dict:
    settings = get_settings()
    def endpoints(model):
        response = httpx.get(f"https://openrouter.ai/api/v1/models/{model}/endpoints", timeout=30)
        response.raise_for_status()
        return response.json()["data"]["endpoints"]

    def select(model, tag, available, allow_unavailable=False):
        endpoint = next((item for item in available if item["tag"] == tag and (item.get("status") == 0 or allow_unavailable)), None)
        if endpoint is None or not {"tools", "response_format", "structured_outputs"}.issubset(endpoint["supported_parameters"]):
            return None
        price = {"model": model, "provider": endpoint["provider_name"], "routing_tag": endpoint["tag"], "prompt": float(endpoint["pricing"]["prompt"]), "completion": float(endpoint["pricing"]["completion"]), "checked_at_utc": datetime.now(UTC).isoformat(), "endpoint": endpoint}
        if price["prompt"] <= 0 or price["completion"] <= 0:
            raise ValueError("Nonpositive or absent endpoint price cannot establish a strict cost bound")
        return price

    primary_endpoints = endpoints(settings.openrouter_model)
    result = select(settings.openrouter_model, settings.openrouter_provider, primary_endpoints, allow_unavailable=True)
    if result is None:
        raise RuntimeError("Primary endpoint is unavailable or lacks required capabilities")
    result["retry_endpoint"] = select(settings.openrouter_model, settings.openrouter_retry_provider, primary_endpoints) if settings.openrouter_retry_provider else None
    result["fallback_endpoint"] = select(settings.openrouter_fallback_model, settings.openrouter_fallback_provider, endpoints(settings.openrouter_fallback_model)) if settings.openrouter_fallback_model and settings.openrouter_fallback_provider else None
    if result["endpoint"].get("status") != 0 and not (result["retry_endpoint"] or result["fallback_endpoint"]):
        raise RuntimeError("Primary endpoint is unavailable and no verified recovery endpoint exists")
    # A run owns its immutable snapshot; a parallel capability probe cannot overwrite it.
    path = PROJECT_ROOT / ".local" / "eval" / ("pricing-" + os.environ.get("EVAL_RUN_ID", uuid4().hex) + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    os.environ["EVAL_PRICE_SNAPSHOT"] = str(path)
    return result


def rates(model: str | None = None, tag: str | None = None) -> dict:
    path = Path(os.environ["EVAL_PRICE_SNAPSHOT"])
    price = json.loads(path.read_text(encoding="utf-8"))
    settings = get_settings()
    if price["model"] != settings.openrouter_model or price["routing_tag"] != settings.openrouter_provider:
        raise ValueError("Pricing snapshot does not match configured model/endpoint")
    if model is not None:
        candidates = [price, price.get("retry_endpoint"), price.get("fallback_endpoint")]
        selected = next((item for item in candidates if item and item["model"] == model and (tag is None or item["routing_tag"] == tag)), None)
        if selected is None:
            raise ValueError("Request model/endpoint has no verified pricing")
        return selected
    return price


def max_call_cost() -> float:
    price = rates()
    return int(os.getenv("EVAL_MAX_REQUEST_BYTES", "65536")) * price["prompt"] + int(os.getenv("OPENROUTER_MAX_OUTPUT_TOKENS", "4096")) * price["completion"]


def fallback_budget() -> float:
    amount = float(os.getenv("EVAL_FALLBACK_BUDGET_USD", "0.10"))
    if not 0 <= amount <= limits()[0]:
        raise ValueError("Fallback budget must be nonnegative and within the total budget")
    return min(amount, limits()[1])


def reserve(payload: dict, size: int, scope: str) -> str:
    if payload.get("stream"):
        raise ValueError("Evaluation cost accounting requires nonstreaming responses")
    maximum = int(os.getenv("EVAL_MAX_REQUEST_BYTES", "65536"))
    if size + 2048 > maximum:
        raise BudgetExceeded("Request exceeds conservative input-byte bound; no HTTP request sent")
    tags = payload.get("provider", {}).get("only", [])
    price = rates(payload.get("model"), tags[0] if len(tags) == 1 else None)
    output_limit = payload.get("max_tokens", payload.get("max_completion_tokens"))
    if not isinstance(output_limit, int) or not 0 < output_limit <= int(os.getenv("OPENROUTER_MAX_OUTPUT_TOKENS", "4096")):
        raise ValueError("Request output tokens must have a bounded limit")
    # UTF-8 byte count plus framing allowance is deliberately conservative, not a measured token count.
    reserved = (size + 2048) * price["prompt"] + output_limit * price["completion"]
    _, stop = limits()
    call_id = uuid4().hex
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        cumulative = database.execute("SELECT COALESCE(SUM(charged_cost),0) FROM calls").fetchone()[0]
        if cumulative + reserved > stop:
            raise BudgetExceeded("Cumulative evaluation budget would cross stop threshold; request blocked")
        context = call_context.get() or {}
        if context.get("fallback_count"):
            extra = database.execute("SELECT COALESCE(SUM(charged_cost),0) FROM calls WHERE run_id=? AND fallback_count=1", (os.environ["EVAL_RUN_ID"],)).fetchone()[0]
            if extra + reserved > fallback_budget():
                raise BudgetExceeded("Run fallback allowance exhausted; recovery request blocked")
        database.execute("INSERT INTO calls (id,started,run_id,case_id,category,scope,model,provider,status,reserved_cost,charged_cost) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (call_id, datetime.now(UTC).isoformat(), os.environ["EVAL_RUN_ID"], os.getenv("EVAL_CASE_KEY", "provider-check"), os.getenv("EVAL_CATEGORY", "verification"), scope, price["model"], price["provider"], "pending_or_unknown", reserved, reserved))
        database.execute("UPDATE calls SET routing_tag=?,prompt_price=?,completion_price=?,logical_call_id=?,primary_model=?,fallback_model=?,fallback_reason=?,fallback_count=? WHERE id=?", (price["routing_tag"], price["prompt"], price["completion"], context.get("logical_call_id", call_id), context.get("primary_model", price["model"]), context.get("fallback_model"), context.get("fallback_reason"), context.get("fallback_count", 0), call_id))
    return call_id


def settle(call_id: str, data: dict, http_status: int) -> None:
    usage = data.get("usage") or {}
    input_tokens = usage.get("prompt_tokens")
    output_tokens = usage.get("completion_tokens")
    actual = usage.get("cost")
    with connection() as database:
        row = database.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
    price = {"prompt": row["prompt_price"], "completion": row["completion_price"], "provider": row["provider"]}
    estimated = input_tokens * price["prompt"] + output_tokens * price["completion"] if isinstance(input_tokens, int) and isinstance(output_tokens, int) else None
    if actual is not None and (not isinstance(actual, (int, float)) or actual < 0):
        raise ValueError("Invalid OpenRouter cost metadata")
    # A rejected HTTP 4xx request has no accepted model generation. Timeouts remain ambiguous.
    if actual is None and http_status in {400, 401, 402, 403, 404, 422, 429} and not usage:
        actual = 0.0
    charged = actual if actual is not None else estimated
    status = "completed" if http_status == 200 and not data.get("error") else "http_error"
    if charged is None:
        status = "pending_or_unknown"
    with connection() as database:
        database.execute("UPDATE calls SET provider=?,status=?,input_tokens=?,output_tokens=?,total_tokens=?,actual_cost=?,estimated_cost=?,charged_cost=COALESCE(?,reserved_cost),generation_id=?,error_type=? WHERE id=?", (data.get("provider", price["provider"]), status, input_tokens, output_tokens, usage.get("total_tokens"), actual, estimated, charged, data.get("id"), str((data.get("error") or {}).get("code", "")) or None, call_id))


def accounting_hooks(scope: str) -> dict:
    def evidence_path(call_id):
        directory = Path(os.getenv("EVAL_RAW_CALL_DIR", str(PROJECT_ROOT / ".local" / "eval" / "http_evidence" / os.environ["EVAL_RUN_ID"])))
        directory.mkdir(parents=True, exist_ok=True)
        return directory / (call_id + ".json")

    def before_request(request):
        payload = json.loads(request.content)
        call_id = reserve(payload, len(request.content), scope)
        request.extensions["evaluation_call_id"] = call_id
        # No HTTP headers are saved: bearer/API keys never belong in exported evidence.
        evidence_path(call_id).write_text(json.dumps({"call_id": call_id, "scope": scope, "logical_call": call_context.get(), "request": payload, "status": "request_reserved"}, ensure_ascii=False, indent=2), encoding="utf-8")

    def after_response(response):
        response.read()
        try:
            data = response.json()
        except ValueError:
            data = {"non_json_response": response.text}
        call_id = response.request.extensions["evaluation_call_id"]
        path = evidence_path(call_id)
        evidence = json.loads(path.read_text(encoding="utf-8"))
        evidence.update({"http_status": response.status_code, "response": data, "status": "response_received"})
        path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        settle(call_id, data, response.status_code)

    return {"request": [before_request], "response": [after_response]}


def calls_for_run(run_id: str) -> list[dict]:
    with connection() as database:
        return [dict(row) for row in database.execute("SELECT * FROM calls WHERE run_id=? ORDER BY started,id", (run_id,))]


def cost_summary(run_id: str) -> dict:
    calls = calls_for_run(run_id)
    with connection() as database:
        cumulative = database.execute("SELECT COALESCE(SUM(charged_cost),0) FROM calls").fetchone()[0]
    budget, stop = limits()

    def total(selected):
        return {"accounted_usd": round(sum(row["charged_cost"] for row in selected), 12), "actual_usd": round(sum(row["actual_cost"] or 0 for row in selected), 12), "estimated_only_usd": round(sum(row["estimated_cost"] or 0 for row in selected if row["actual_cost"] is None), 12), "unknown_reserve_usd": round(sum(row["charged_cost"] for row in selected if row["status"] == "pending_or_unknown"), 12), "calls": len(selected), "input_tokens": sum(row["input_tokens"] or 0 for row in selected), "output_tokens": sum(row["output_tokens"] or 0 for row in selected), "total_tokens": sum(row["total_tokens"] or 0 for row in selected), "usage_missing_calls": sum(row["input_tokens"] is None or row["output_tokens"] is None for row in selected), "fallback_count": sum(row["fallback_count"] or 0 for row in selected), "fallback_extra_accounted_usd": round(sum(row["charged_cost"] for row in selected if row["fallback_count"]), 12), "fallback_extra_actual_usd": round(sum(row["actual_cost"] or 0 for row in selected if row["fallback_count"]), 12)}

    return {"run": total(calls), "application": total([row for row in calls if row["scope"] == "application"]), "judge": total([row for row in calls if row["scope"] == "judge"]), "by_model": {name: total([row for row in calls if row["model"] == name]) for name in sorted({row["model"] for row in calls})}, "by_provider": {name: total([row for row in calls if row["provider"] == name]) for name in sorted({row["provider"] for row in calls})}, "by_category": {name: total([row for row in calls if row["category"] == name]) for name in sorted({row["category"] for row in calls})}, "by_case": {name: total([row for row in calls if row["case_id"] == name]) for name in sorted({row["case_id"] for row in calls})}, "cumulative_accounted_usd": round(cumulative, 12), "budget_usd": budget, "stop_usd": stop, "remaining_budget_usd": round(budget - cumulative, 12), "remaining_before_stop_usd": round(stop - cumulative, 12), "scope": "All OpenRouter evaluation and verification calls since Phase 1.5 ledger creation. Prior providers and unchanged external embedding charges are unknown and excluded; unknown OpenRouter calls retain worst-case reserves."}


def preflight(cases: list[dict]) -> dict:
    # Current Customer pipeline: planner + claims + validation + judge. Support: tool-budget turns plus terminal turns.
    calls = sum(4 if case["category"] == "rag" else int(os.getenv("SUPPORT_MAX_TOOL_CALLS", "6")) + 2 if case["category"] == "workflow" else 0 for case in cases)
    price = rates()
    recovery = price.get("retry_endpoint") or price.get("fallback_endpoint")
    recovery_maximum = int(os.getenv("EVAL_MAX_REQUEST_BYTES", "65536")) * recovery["prompt"] + int(os.getenv("OPENROUTER_MAX_OUTPUT_TOKENS", "4096")) * recovery["completion"] if recovery else 0
    extra_bound = min(calls * recovery_maximum, fallback_budget())
    bound = calls * max_call_cost() + extra_bound
    summary = cost_summary(os.environ["EVAL_RUN_ID"])
    if bound > summary["remaining_before_stop_usd"]:
        raise BudgetExceeded("Complete run conservative reservation exceeds remaining cumulative budget")
    return {"planned_call_bound": calls, "primary_bound_usd": calls * max_call_cost(), "fallback_allowance_usd": extra_bound, "conservative_run_bound_usd": bound, "remaining_before_stop_usd": summary["remaining_before_stop_usd"], "assumption": "Full request-byte cap and full output limit per planned logical call. At most one recovery per call, bounded by the run fallback allowance and cumulative stop. Exhausted allowance blocks recovery and records the case failure; it never silently omits a case. Per-call transactional reserve remains authoritative."}
