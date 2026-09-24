import json
import time
from uuid import uuid4

from langsmith import traceable

from backend.app.database import get_connection
from backend.app.models import UserContext
from backend.app.stock import assess_stock_facts
from backend.app.support_evidence import EvidenceRecord
from backend.app.support_tools import ToolResult, execute_tool_batch
from backend.app.trace import current_trace_id


def values_match(actual: dict[str, object], expected: dict[str, object], fields: tuple[str, ...]) -> bool:
    for field in fields:
        if actual.get(field) != expected.get(field):
            return False
    return True


def all_checks_passed(checks: dict[str, bool]) -> bool:
    for passed in checks.values():
        if not passed:
            return False
    return True


def index_tool_results(evidence: list[EvidenceRecord]) -> dict[str, EvidenceRecord]:
    results: dict[str, EvidenceRecord] = {}
    for record in evidence:
        results[record.tool_name] = record
    return results


def collect_evidence_ids(evidence: list[EvidenceRecord]) -> list[str]:
    evidence_ids: list[str] = []
    for record in evidence:
        evidence_ids.append(record.evidence_id)
    return evidence_ids


def verify_stock_facts(merchant: dict[str, object], warehouse: dict[str, object] | None, platform: dict[str, object] | None, **options) -> dict[str, object]:
    return assess_stock_facts(merchant, warehouse, platform, **options)


def verify_order_facts(platform: ToolResult, merchant: ToolResult, expected: dict[str, object]) -> dict[str, object]:
    quantity_matches = merchant.response.get("quantity") == expected.get("quantity")
    source_quantity_matches = merchant.response.get("source_quantity") == expected.get("quantity")
    amount_matches = merchant.response.get("amount_minor") == expected.get("amount_minor")
    source_amount_matches = merchant.response.get("source_amount_minor") == expected.get("amount_minor")

    checks = {
        "platform_available": platform.status == "success",
        "merchant_available": merchant.status == "success",
        "event_matches": merchant.response.get("event_id") == expected.get("event_id"),
        "sku_matches": merchant.response.get("platform_sku") == expected.get("sku"),
        "quantity_matches": quantity_matches and source_quantity_matches,
        "amount_matches": amount_matches and source_amount_matches,
        "exactly_one_merchant_order": merchant.response.get("merchant_order_count") == 1,
        "task_completed": merchant.response.get("task_status") == "completed",
    }
    return {"resolved": all_checks_passed(checks), "checks": checks}


def verify_shipment_facts(warehouse: ToolResult, merchant: ToolResult, platform: ToolResult, expected: dict[str, object]) -> dict[str, object]:
    shipment_fields = ("shipment_id", "carrier", "tracking_number")
    checks = {
        "warehouse_available": warehouse.status == "success",
        "merchant_available": merchant.status == "success",
        "platform_available": platform.status == "success",
        "one_actual_warehouse_shipment": warehouse.response.get("shipment_count") == 1,
        "warehouse_matches": values_match(warehouse.response, expected, shipment_fields),
        "merchant_matches": values_match(merchant.response, expected, shipment_fields),
        "platform_matches": values_match(platform.response, expected, shipment_fields),
        "recovery_task_completed": merchant.response.get("task_status") == "completed",
    }
    return {"resolved": all_checks_passed(checks), "checks": checks}


def save_verification(action_id: str, status: str, details: dict[str, object], evidence_ids: list[str]) -> None:
    with get_connection() as connection:
        connection.execute(
            """INSERT INTO support.action_verifications (verification_id, action_id, status, details, evidence_ids, trace_id)
            VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s)
            ON CONFLICT (action_id) DO UPDATE SET status = EXCLUDED.status, details = EXCLUDED.details, evidence_ids = EXCLUDED.evidence_ids, trace_id = EXCLUDED.trace_id, checked_at = NOW()""",
            (str(uuid4()), action_id, status, json.dumps(details), json.dumps(evidence_ids), current_trace_id()),
        )


@traceable(name="verify_order_recovery", run_type="chain")
def verify_order_recovery(user: UserContext, action_id: str, final: bool = False) -> dict[str, object]:
    with get_connection() as connection:
        action = connection.execute("SELECT action_id::text, case_id::text, company_id, shop_id, external_order_id, source_snapshot FROM support.action_proposals WHERE action_id = %s AND company_id = %s", (action_id, user.company_id)).fetchone()
    if action is None:
        raise PermissionError("Action is not available in this company scope")
    calls = [
        {"name": "GetOrder", "args": {"shop_id": action["shop_id"], "order_id": action["external_order_id"]}, "id": "verify-platform"},
        {"name": "GetProcessRecords", "args": {"shop_id": action["shop_id"], "order_id": action["external_order_id"]}, "id": "verify-merchant"},
    ]
    evidence = execute_tool_batch(action["case_id"], user, calls, action["shop_id"], action["external_order_id"])
    facts = index_tool_results(evidence)
    platform = facts["GetOrder"]
    merchant = facts["GetProcessRecords"]
    snapshot = action["source_snapshot"]
    verification = verify_order_facts(platform, merchant, snapshot)
    checks = verification["checks"]
    resolved = verification["resolved"]
    if resolved:
        status = "verified_resolved"
    elif final:
        status = "verification_failed"
    else:
        status = "pending"

    evidence_ids = collect_evidence_ids(evidence)
    details = {"checks": checks, "order_id": action["external_order_id"], "shop_id": action["shop_id"]}
    save_verification(action_id, status, details, evidence_ids)
    if resolved or final:
        with get_connection() as connection:
            connection.execute("UPDATE support.action_proposals SET status = %s, updated_at = NOW() WHERE action_id = %s", (status, action_id))
            connection.execute("INSERT INTO support.action_steps (step_id, action_id, step_name, status, details, evidence_id, trace_id) VALUES (%s, %s, 'verification', %s, %s::jsonb, %s, %s)", (str(uuid4()), action_id, status, json.dumps(details), evidence_ids[0], current_trace_id()))
    from backend.app.actions import show_action

    return show_action(user, action_id)


def wait_for_order_verification(user: UserContext, action_id: str, timeout_seconds: float = 6) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = verify_order_recovery(user, action_id)
        if result["status"] == "verified_resolved":
            return result
        time.sleep(0.25)
    return verify_order_recovery(user, action_id, final=True)


@traceable(name="verify_shipment_recovery", run_type="chain")
def verify_shipment_recovery(user: UserContext, action_id: str) -> dict[str, object]:
    with get_connection() as connection:
        action = connection.execute("SELECT action_id::text, case_id::text, company_id, shop_id, external_order_id, source_snapshot FROM support.action_proposals WHERE action_id = %s AND company_id = %s AND action_type = 'recover_shipment'", (action_id, user.company_id)).fetchone()
    if action is None:
        raise PermissionError("Shipment action is not available in this company scope")
    calls = [
        {"name": "GetShipment", "args": {"shop_id": action["shop_id"], "order_id": action["external_order_id"]}, "id": "verify-warehouse-shipment"},
        {"name": "GetShipmentRecords", "args": {"shop_id": action["shop_id"], "order_id": action["external_order_id"]}, "id": "verify-merchant-shipment"},
        {"name": "GetPlatformShipment", "args": {"shop_id": action["shop_id"], "order_id": action["external_order_id"]}, "id": "verify-platform-shipment"},
    ]
    evidence = execute_tool_batch(action["case_id"], user, calls, action["shop_id"], action["external_order_id"])
    facts = index_tool_results(evidence)
    warehouse = facts["GetShipment"]
    merchant = facts["GetShipmentRecords"]
    platform = facts["GetPlatformShipment"]
    expected = action["source_snapshot"]
    verification = verify_shipment_facts(warehouse, merchant, platform, expected)
    checks = verification["checks"]
    resolved = verification["resolved"]
    if resolved:
        status = "verified_resolved"
    else:
        status = "pending"

    evidence_ids = collect_evidence_ids(evidence)
    details = {"checks": checks, "order_id": action["external_order_id"], "shop_id": action["shop_id"]}
    save_verification(action_id, status, details, evidence_ids)
    if resolved:
        with get_connection() as connection:
            connection.execute("UPDATE support.action_proposals SET status = 'verified_resolved', updated_at = NOW() WHERE action_id = %s", (action_id,))
            connection.execute("INSERT INTO support.action_steps (step_id, action_id, step_name, status, details, evidence_id, trace_id) VALUES (%s, %s, 'verification', 'verified_resolved', %s::jsonb, %s, %s)", (str(uuid4()), action_id, json.dumps(details), evidence_ids[0], current_trace_id()))
    from backend.app.actions import show_action

    return show_action(user, action_id)


def wait_for_shipment_verification(user: UserContext, action_id: str, timeout_seconds: float = 15) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = verify_shipment_recovery(user, action_id)
        if result["status"] == "verified_resolved":
            return result
        time.sleep(0.25)
    return verify_shipment_recovery(user, action_id)
