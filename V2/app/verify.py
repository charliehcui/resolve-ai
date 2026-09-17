import json
import time
from uuid import uuid4

from langsmith import traceable

from app.db import get_connection
from app.models import AuthContext
from app.tools import execute_tool_batch
from app.trace import current_trace_id


def save_verification(action_id: str, status: str, details: dict[str, object], evidence_ids: list[str]) -> None:
    with get_connection() as connection:
        connection.execute(
            """INSERT INTO support.action_verifications (verification_id, action_id, status, details, evidence_ids, trace_id)
            VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s)
            ON CONFLICT (action_id) DO UPDATE SET status = EXCLUDED.status, details = EXCLUDED.details, evidence_ids = EXCLUDED.evidence_ids, trace_id = EXCLUDED.trace_id, checked_at = NOW()""",
            (str(uuid4()), action_id, status, json.dumps(details), json.dumps(evidence_ids), current_trace_id()),
        )


@traceable(name="verify_order_recovery", run_type="chain")
def verify_order_recovery(auth: AuthContext, action_id: str, final: bool = False) -> dict[str, object]:
    with get_connection() as connection:
        action = connection.execute("SELECT action_id::text, case_id::text, company_id, shop_id, external_order_id, source_snapshot FROM support.action_proposals WHERE action_id = %s AND company_id = %s", (action_id, auth.company_id)).fetchone()
    if action is None:
        raise PermissionError("Action is not available in this company scope")
    calls = [
        {"name": "GetOrder", "args": {"shop_id": action["shop_id"], "order_id": action["external_order_id"]}, "id": "verify-platform"},
        {"name": "GetProcessRecords", "args": {"shop_id": action["shop_id"], "order_id": action["external_order_id"]}, "id": "verify-merchant"},
    ]
    evidence = execute_tool_batch(action["case_id"], auth, calls, action["shop_id"], action["external_order_id"])
    facts = {record.tool_name: record for record in evidence}
    platform = facts["GetOrder"]
    merchant = facts["GetProcessRecords"]
    snapshot = action["source_snapshot"]
    checks = {
        "platform_available": platform.status == "success",
        "merchant_available": merchant.status == "success",
        "event_matches": merchant.response.get("event_id") == snapshot.get("event_id"),
        "sku_matches": merchant.response.get("platform_sku") == snapshot.get("sku"),
        "quantity_matches": merchant.response.get("quantity") == snapshot.get("quantity") and merchant.response.get("source_quantity") == snapshot.get("quantity"),
        "amount_matches": merchant.response.get("amount_minor") == snapshot.get("amount_minor") and merchant.response.get("source_amount_minor") == snapshot.get("amount_minor"),
        "exactly_one_merchant_order": merchant.response.get("merchant_order_count") == 1,
        "task_completed": merchant.response.get("task_status") == "completed",
    }
    resolved = all(checks.values())
    status = "verified_resolved" if resolved else ("verification_failed" if final else "pending")
    evidence_ids = [record.evidence_id for record in evidence]
    details = {"checks": checks, "order_id": action["external_order_id"], "shop_id": action["shop_id"]}
    save_verification(action_id, status, details, evidence_ids)
    if resolved or final:
        with get_connection() as connection:
            connection.execute("UPDATE support.action_proposals SET status = %s, updated_at = NOW() WHERE action_id = %s", (status, action_id))
            connection.execute("INSERT INTO support.action_steps (step_id, action_id, step_name, status, details, evidence_id, trace_id) VALUES (%s, %s, 'verification', %s, %s::jsonb, %s, %s)", (str(uuid4()), action_id, status, json.dumps(details), evidence_ids[0], current_trace_id()))
    from app.actions import show_action

    return show_action(auth, action_id)


def wait_for_order_verification(auth: AuthContext, action_id: str, timeout_seconds: float = 6) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = verify_order_recovery(auth, action_id)
        if result["status"] == "verified_resolved":
            return result
        time.sleep(0.25)
    return verify_order_recovery(auth, action_id, final=True)
