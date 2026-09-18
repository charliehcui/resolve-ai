import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
from langsmith import traceable

from backend.app.database import get_connection
from backend.app.models import AuthContext
from backend.app.support_cases import update_case
from backend.app.support_evidence import EvidenceRecord, save_evidence
from backend.app.support_tools import ToolResult, execute_tool_batch, get_order, get_platform_shipment, get_shipment, get_shipment_records, get_shop_status, request_fact
from backend.app.trace import current_trace_id
from simulator.services.common import read_service_token

APPROVAL_MINUTES = 10
EXECUTION_LEASE_SECONDS = 30


def save_action_step(action_id: str, step_name: str, status: str, details: dict[str, object], evidence_id: str | None = None) -> None:
    with get_connection() as connection:
        connection.execute("INSERT INTO support.action_steps (step_id, action_id, step_name, status, details, evidence_id, trace_id) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)", (str(uuid4()), action_id, step_name, status, json.dumps(details, default=str), evidence_id, current_trace_id()))


def load_case_scope(case_id: str, auth: AuthContext) -> dict[str, object]:
    with get_connection() as connection:
        row = connection.execute("""SELECT c.case_id::text, c.conversation_id::text, c.company_id, h.known_shop_id, h.known_order_id
            FROM support.cases c JOIN support.handoffs h ON h.handoff_id = c.handoff_id
            JOIN support.conversations v ON v.conversation_id = c.conversation_id
            WHERE c.case_id = %s AND c.company_id = %s AND v.user_id = %s""", (case_id, auth.company_id, auth.user_id)).fetchone()
    if row is None:
        raise PermissionError("Support case is not available in this user scope")
    if not row["known_shop_id"] or not row["known_order_id"]:
        raise ValueError("Case requires known shop_id and order_id")
    return dict(row)


def get_sku_mapping(auth: AuthContext, shop_id: str, platform_sku: str) -> ToolResult:
    base_url = os.getenv("MERCHANT_URL", "http://127.0.0.1:8002")
    result = request_fact("GetSkuMapping", "merchant", f"{base_url}/internal/shops/{shop_id}/mappings/{platform_sku}", auth.company_id, {})
    result.request = {"shop_id": shop_id, "platform_sku": platform_sku}
    return result


def save_tool_evidence(case_id: str, auth: AuthContext, result: ToolResult) -> str:
    record = save_evidence(case_id, auth.company_id, str(uuid4()), False, None, result.tool_name, result.request, result.response, result.source_service, result.source_record_id, result.status, result.latency_ms, result.trace_id)
    return record.evidence_id


def evidence_by_tool(records: list[EvidenceRecord]) -> dict[str, EvidenceRecord]:
    indexed_records: dict[str, EvidenceRecord] = {}
    for record in records:
        indexed_records[record.tool_name] = record
    return indexed_records


def evidence_ids_from(records: list[EvidenceRecord]) -> list[str]:
    evidence_ids: list[str] = []
    for record in records:
        evidence_ids.append(record.evidence_id)
    return evidence_ids


def total_evidence_latency(records: list[EvidenceRecord]) -> int:
    total_latency_ms = 0
    for record in records:
        total_latency_ms += record.latency_ms
    return total_latency_ms


def fields_match(actual: dict[str, object], expected: dict[str, object], fields: tuple[str, ...]) -> bool:
    for field in fields:
        if actual.get(field) != expected.get(field):
            return False
    return True


def action_idempotency_key(company_id: str, shop_id: str, external_order_id: str, event_id: str, source_version: int, enable_order_sync: bool) -> str:
    value = f"recover_order:{company_id}:{shop_id}:{external_order_id}:{event_id}:{source_version}:{enable_order_sync}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def shipment_action_idempotency_key(company_id: str, shop_id: str, external_order_id: str, shipment_id: str, source_version: int, enable_shipment_sync: bool) -> str:
    value = f"recover_shipment:{company_id}:{shop_id}:{external_order_id}:{shipment_id}:{source_version}:{enable_shipment_sync}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@traceable(name="propose_order_recovery", run_type="chain")
def propose_order_recovery(auth: AuthContext, case_id: str, enable_order_sync: bool = False) -> dict[str, object]:
    scope = load_case_scope(case_id, auth)
    shop_id = str(scope["known_shop_id"])
    order_id = str(scope["known_order_id"])
    calls = [
        {"name": "GetOrder", "args": {"shop_id": shop_id, "order_id": order_id}, "id": "proposal-order"},
        {"name": "GetProcessRecords", "args": {"shop_id": shop_id, "order_id": order_id}, "id": "proposal-process"},
        {"name": "GetShopStatus", "args": {"shop_id": shop_id}, "id": "proposal-shop"},
        {"name": "CheckConnection", "args": {"shop_id": shop_id}, "id": "proposal-connection"},
    ]
    evidence = execute_tool_batch(case_id, auth, calls, shop_id, order_id)
    facts = evidence_by_tool(evidence)
    source = facts["GetOrder"]
    process = facts["GetProcessRecords"]
    shop = facts["GetShopStatus"]
    connection_status = facts["CheckConnection"]
    if source.status != "success":
        raise ValueError("Recoverable source order was not found")
    source_data = source.response
    if source_data.get("payment_status") != "paid":
        raise ValueError("Only paid source orders can be recovered")
    if process.status == "success" and process.response.get("merchant_order_id"):
        update_case(case_id, "diagnosed", "订单已满足目标状态，无需创建恢复方案。", len(evidence), total_evidence_latency(evidence))
        return {"status": "no_action_needed", "case_id": case_id, "evidence_ids": evidence_ids_from(evidence)}
    if shop.status != "success" or connection_status.status != "success":
        raise ValueError("Shop state could not be verified")
    if not shop.response.get("sync_enabled") and not enable_order_sync:
        raise ValueError("Order sync is disabled; enabling it requires an explicit proposal option")
    if connection_status.response.get("channel") == "B" and connection_status.response.get("connection_status") != "authorized":
        raise ValueError("Shop authorization cannot be repaired by recover_order")
    mapping = get_sku_mapping(auth, shop_id, str(source_data["sku"]))
    mapping_evidence_id = save_tool_evidence(case_id, auth, mapping)
    if mapping.status != "success" or not mapping.response.get("active"):
        raise ValueError("SKU mapping must exist before order recovery")
    snapshot_fields = ("event_id", "version", "sku", "quantity", "amount_minor", "payment_status")
    snapshot: dict[str, object] = {}
    for field in snapshot_fields:
        snapshot[field] = source_data[field]

    evidence_ids = evidence_ids_from(evidence)
    evidence_ids.append(mapping_evidence_id)
    idempotency_key = action_idempotency_key(auth.company_id, shop_id, order_id, str(source_data["event_id"]), int(source_data["version"]), enable_order_sync)
    action_id = str(uuid4())
    expires_at = datetime.now(UTC) + timedelta(minutes=APPROVAL_MINUTES)
    with get_connection() as connection:
        existing = connection.execute("SELECT action_id::text FROM support.action_proposals WHERE idempotency_key = %s AND company_id = %s", (idempotency_key, auth.company_id)).fetchone()
        if existing:
            return show_action(auth, existing["action_id"])
        connection.execute(
            """INSERT INTO support.action_proposals (action_id, case_id, company_id, shop_id, external_order_id, action_type, status, source_event_id, source_version, shop_version, source_snapshot, enable_order_sync, evidence_ids, idempotency_key, proposed_by, expires_at)
            VALUES (%s, %s, %s, %s, %s, 'recover_order', 'proposed', %s, %s, %s, %s::jsonb, %s, %s::jsonb, %s, %s, %s)""",
            (action_id, case_id, auth.company_id, shop_id, order_id, source_data["event_id"], source_data["version"], shop.response["version"], json.dumps(snapshot), enable_order_sync, json.dumps(evidence_ids), idempotency_key, auth.user_id, expires_at),
        )
    save_action_step(action_id, "action_proposal", "passed", {"action_type": "recover_order", "shop_id": shop_id, "order_id": order_id, "enable_order_sync": enable_order_sync, "evidence_ids": evidence_ids}, source.evidence_id)
    save_action_step(action_id, "policy_check", "passed", {"payment_status": "paid", "mapping_active": True, "connection_authorized": True, "source_version": source_data["version"], "shop_version": shop.response["version"], "evidence_ids": evidence_ids}, mapping_evidence_id)
    return show_action(auth, action_id)


@traceable(name="propose_shipment_recovery", run_type="chain")
def propose_shipment_recovery(auth: AuthContext, case_id: str, enable_shipment_sync: bool = False) -> dict[str, object]:
    scope = load_case_scope(case_id, auth)
    shop_id = str(scope["known_shop_id"])
    order_id = str(scope["known_order_id"])
    calls = [
        {"name": "GetOrder", "args": {"shop_id": shop_id, "order_id": order_id}, "id": "proposal-source-order"},
        {"name": "GetShipment", "args": {"shop_id": shop_id, "order_id": order_id}, "id": "proposal-warehouse-shipment"},
        {"name": "GetShipmentRecords", "args": {"shop_id": shop_id, "order_id": order_id}, "id": "proposal-merchant-shipment"},
        {"name": "GetPlatformShipment", "args": {"shop_id": shop_id, "order_id": order_id}, "id": "proposal-platform-shipment"},
        {"name": "GetShopStatus", "args": {"shop_id": shop_id}, "id": "proposal-shop"},
    ]
    evidence = execute_tool_batch(case_id, auth, calls, shop_id, order_id)
    facts = evidence_by_tool(evidence)
    source_order = facts["GetOrder"]
    warehouse = facts["GetShipment"]
    merchant = facts["GetShipmentRecords"]
    platform = facts["GetPlatformShipment"]
    shop = facts["GetShopStatus"]
    if source_order.status != "success" or source_order.response.get("payment_status") != "paid":
        raise ValueError("Only a currently paid source order can receive shipment recovery")
    if warehouse.status != "success" or not warehouse.response.get("shipment_id") or warehouse.response.get("shipment_count") != 1:
        raise ValueError("Exactly one warehouse shipment fact is required")
    if merchant.status != "success" or merchant.response.get("shipment_id") != warehouse.response.get("shipment_id"):
        raise ValueError("Merchant must hold the matching warehouse shipment before recovery")
    expected = {"shipment_id": warehouse.response["shipment_id"], "carrier": warehouse.response["carrier"], "tracking_number": warehouse.response["tracking_number"]}
    expected_fields = ("shipment_id", "carrier", "tracking_number")
    if platform.status == "success" and fields_match(platform.response, expected, expected_fields):
        update_case(case_id, "diagnosed", "平台发货状态已满足目标，无需创建恢复方案。", len(evidence), total_evidence_latency(evidence))
        return {"status": "no_action_needed", "case_id": case_id, "evidence_ids": evidence_ids_from(evidence)}
    if platform.status not in {"not_found", "empty"}:
        raise ValueError("Platform shipment state conflicts with the warehouse fact")
    if shop.status != "success":
        raise ValueError("Shop state could not be verified")
    if not shop.response.get("shipment_sync_enabled") and not enable_shipment_sync:
        raise ValueError("Shipment sync is disabled; enabling it requires an explicit proposal option")
    snapshot = {**expected, "version": warehouse.response["shipment_version"], "shipped_at": warehouse.response["shipped_at"], "order_event_id": source_order.response["event_id"], "order_version": source_order.response["version"], "payment_status": source_order.response["payment_status"]}
    evidence_ids = evidence_ids_from(evidence)
    idempotency_key = shipment_action_idempotency_key(auth.company_id, shop_id, order_id, str(snapshot["shipment_id"]), int(snapshot["version"]), enable_shipment_sync)
    action_id = str(uuid4())
    expires_at = datetime.now(UTC) + timedelta(minutes=APPROVAL_MINUTES)
    with get_connection() as connection:
        existing = connection.execute("SELECT action_id::text FROM support.action_proposals WHERE idempotency_key = %s AND company_id = %s", (idempotency_key, auth.company_id)).fetchone()
        if existing:
            return show_action(auth, existing["action_id"])
        connection.execute("""INSERT INTO support.action_proposals
            (action_id, case_id, company_id, shop_id, external_order_id, action_type, status, source_event_id, source_version, shop_version, source_snapshot, enable_shipment_sync, evidence_ids, idempotency_key, proposed_by, expires_at)
            VALUES (%s, %s, %s, %s, %s, 'recover_shipment', 'proposed', %s, %s, %s, %s::jsonb, %s, %s::jsonb, %s, %s, %s)""", (action_id, case_id, auth.company_id, shop_id, order_id, snapshot["shipment_id"], snapshot["version"], shop.response["version"], json.dumps(snapshot, default=str), enable_shipment_sync, json.dumps(evidence_ids), idempotency_key, auth.user_id, expires_at))
    steps: list[str] = []
    if enable_shipment_sync:
        steps.append("enable_shipment_sync")
    steps.append("resend_specific_shipment")
    save_action_step(action_id, "action_proposal", "passed", {"action_type": "recover_shipment", "shop_id": shop_id, "order_id": order_id, "steps": steps, "evidence_ids": evidence_ids}, warehouse.evidence_id)
    save_action_step(action_id, "policy_check", "passed", {"warehouse_shipment_count": 1, "merchant_matches": True, "source_version": snapshot["version"], "shop_version": shop.response["version"], "evidence_ids": evidence_ids}, merchant.evidence_id)
    return show_action(auth, action_id)


def get_action(auth: AuthContext, action_id: str) -> dict[str, object]:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM support.action_proposals WHERE action_id = %s AND company_id = %s", (action_id, auth.company_id)).fetchone()
    if row is None:
        raise PermissionError("Action is not available in this company scope")
    return dict(row)


def show_action(auth: AuthContext, action_id: str) -> dict[str, object]:
    action = get_action(auth, action_id)
    with get_connection() as connection:
        decision = connection.execute("SELECT decision_id::text, decision, decided_by, decided_at FROM support.action_decisions WHERE action_id = %s", (action_id,)).fetchone()
        execution = connection.execute("SELECT execution_id::text, request_id::text, status, claim_until, attempts, receipt, error_type, trace_id, created_at, updated_at FROM support.action_executions WHERE action_id = %s", (action_id,)).fetchone()
        verification = connection.execute("SELECT verification_id::text, status, details, evidence_ids, trace_id, checked_at FROM support.action_verifications WHERE action_id = %s", (action_id,)).fetchone()
        steps = connection.execute("SELECT step_id::text, step_name, status, details, evidence_id::text, trace_id, created_at FROM support.action_steps WHERE action_id = %s ORDER BY created_at", (action_id,)).fetchall()
    result = dict(action)
    result["action_id"] = str(result["action_id"])
    result["case_id"] = str(result["case_id"])
    result["source_event_id"] = str(result["source_event_id"])
    result["decision"] = dict(decision) if decision else None
    result["execution"] = dict(execution) if execution else None
    result["verification"] = dict(verification) if verification else None
    result_steps: list[dict[str, object]] = []
    for step in steps:
        result_steps.append(dict(step))
    result["steps"] = result_steps
    return result


@traceable(name="submit_order_repair", run_type="tool")
def submit_order_repair(payload: dict[str, object]) -> dict[str, object]:
    base_url = os.getenv("MERCHANT_URL", "http://127.0.0.1:8002")
    response = httpx.post(f"{base_url}/repairs/orders", json=payload, headers={"X-Service-Token": read_service_token("support-write")}, timeout=5)
    if not response.is_success:
        raise RuntimeError(f"Merchant repair request failed with HTTP {response.status_code}")
    return response.json()


@traceable(name="submit_shipment_repair", run_type="tool")
def submit_shipment_repair(payload: dict[str, object]) -> dict[str, object]:
    base_url = os.getenv("MERCHANT_URL", "http://127.0.0.1:8002")
    response = httpx.post(f"{base_url}/repairs/shipments", json=payload, headers={"X-Service-Token": read_service_token("support-write")}, timeout=5)
    if not response.is_success:
        raise RuntimeError(f"Merchant shipment repair request failed with HTTP {response.status_code}")
    return response.json()


def block_action(action_id: str, reason: str, evidence_id: str | None = None) -> None:
    with get_connection() as connection:
        connection.execute("UPDATE support.action_proposals SET status = 'blocked', updated_at = NOW() WHERE action_id = %s", (action_id,))
    evidence_ids = []
    if evidence_id:
        evidence_ids.append(evidence_id)
    save_action_step(action_id, "scope_version_recheck", "blocked", {"reason": reason, "evidence_ids": evidence_ids}, evidence_id)


def claim_action_execution(action_id: str, request_id: str) -> str:
    now = datetime.now(UTC)
    claim_until = now + timedelta(seconds=EXECUTION_LEASE_SECONDS)
    with get_connection() as connection:
        approved = connection.execute("""SELECT 1 FROM support.action_proposals p JOIN support.action_decisions d ON d.action_id = p.action_id
            WHERE p.action_id = %s AND p.status IN ('approved', 'executing') AND d.decision = 'approved'""", (action_id,)).fetchone()
        if approved is None:
            raise ValueError("Action does not have a valid approval")
        execution = connection.execute("SELECT execution_id::text, status, claim_until FROM support.action_executions WHERE action_id = %s FOR UPDATE", (action_id,)).fetchone()
        if execution is None:
            connection.execute("INSERT INTO support.action_executions (execution_id, action_id, request_id, status, claim_until, trace_id) VALUES (%s, %s, %s, 'claimed', %s, %s)", (str(uuid4()), action_id, request_id, claim_until, current_trace_id()))
        elif execution["status"] == "submitted":
            return "submitted"
        elif execution["status"] == "claimed" and execution["claim_until"] > now:
            return "busy"
        else:
            connection.execute("UPDATE support.action_executions SET status = 'claimed', claim_until = %s, attempts = attempts + 1, error_type = NULL, trace_id = %s, updated_at = NOW() WHERE action_id = %s", (claim_until, current_trace_id(), action_id))
        connection.execute("UPDATE support.action_proposals SET status = 'executing', updated_at = NOW() WHERE action_id = %s", (action_id,))
    return "acquired"


def reconcile_action_receipt(action: dict[str, object]) -> dict[str, object] | None:
    base_url = os.getenv("MERCHANT_URL", "http://127.0.0.1:8002")
    resource = "shipments" if action["action_type"] == "recover_shipment" else "orders"
    result = request_fact("GetRecoveryReceipt", "merchant", f"{base_url}/repairs/{resource}/{action['action_id']}", str(action["company_id"]), {})
    if result.status == "success":
        return result.response
    return None


def record_reconciled_submission(action: dict[str, object], receipt: dict[str, object]) -> None:
    with get_connection() as connection:
        connection.execute("UPDATE support.action_executions SET status = 'submitted', receipt = %s::jsonb, updated_at = NOW() WHERE action_id = %s", (json.dumps(receipt, default=str), action["action_id"]))
        connection.execute("UPDATE support.action_proposals SET status = 'awaiting_verification', updated_at = NOW() WHERE action_id = %s", (action["action_id"],))
    save_action_step(str(action["action_id"]), "receipt_reconciliation", "accepted", {"recovered_after_unknown_result": True, "task_id": receipt.get("task_id")})


@traceable(name="execute_order_recovery", run_type="chain")
def execute_order_recovery(auth: AuthContext, action_id: str) -> dict[str, object]:
    if auth.role != "admin":
        raise PermissionError("Only a company admin can execute an approved action")
    action = get_action(auth, action_id)
    if action["status"] in {"verified_resolved", "verification_failed", "blocked", "rejected", "expired"}:
        return show_action(auth, action_id)
    if action["status"] == "awaiting_verification":
        from backend.app.verification import wait_for_order_verification

        return wait_for_order_verification(auth, action_id)
    if action["status"] == "executing":
        receipt = reconcile_action_receipt(action)
        if receipt:
            record_reconciled_submission(action, receipt)
            from backend.app.verification import wait_for_order_verification

            return wait_for_order_verification(auth, action_id)
    if action["status"] not in {"approved", "executing"}:
        raise ValueError("Action is not approved")
    current_order = get_order(auth, action["shop_id"], action["external_order_id"])
    current_shop = get_shop_status(auth, action["shop_id"])
    order_evidence_id = save_tool_evidence(str(action["case_id"]), auth, current_order)
    shop_evidence_id = save_tool_evidence(str(action["case_id"]), auth, current_shop)
    snapshot = action["source_snapshot"]
    compared_fields = ("event_id", "version", "sku", "quantity", "amount_minor", "payment_status")
    source_order_matches = current_order.status == "success" and fields_match(current_order.response, snapshot, compared_fields)
    if not source_order_matches:
        block_action(action_id, "SOURCE_VERSION_CHANGED", order_evidence_id)
        return show_action(auth, action_id)
    if current_order.response.get("payment_status") != "paid":
        block_action(action_id, "ORDER_NOT_PAID", order_evidence_id)
        return show_action(auth, action_id)
    if current_shop.status != "success" or current_shop.response.get("version") != action["shop_version"]:
        block_action(action_id, "SHOP_VERSION_CHANGED", shop_evidence_id)
        return show_action(auth, action_id)
    request_id = str(uuid5(NAMESPACE_URL, action["idempotency_key"]))
    with get_connection() as connection:
        decision = connection.execute("SELECT decision_id::text, decided_by FROM support.action_decisions WHERE action_id = %s AND decision = 'approved'", (action_id,)).fetchone()
        if decision is None:
            raise ValueError("Approved decision record is missing")
    claim = claim_action_execution(action_id, request_id)
    if claim == "busy":
        return show_action(auth, action_id)
    if claim == "submitted":
        from backend.app.verification import wait_for_order_verification

        return wait_for_order_verification(auth, action_id)
    save_action_step(action_id, "scope_version_recheck", "passed", {"source_version": action["source_version"], "shop_version": action["shop_version"], "evidence_ids": [order_evidence_id, shop_evidence_id]}, order_evidence_id)
    payload = {
        "action_id": action_id,
        "request_id": request_id,
        "company_id": action["company_id"],
        "shop_id": action["shop_id"],
        "external_order_id": action["external_order_id"],
        "source_event_id": str(action["source_event_id"]),
        "source_version": action["source_version"],
        "shop_version": action["shop_version"],
        "source_snapshot": snapshot,
        "enable_order_sync": action["enable_order_sync"],
        "approval_id": decision["decision_id"],
        "approved_by": decision["decided_by"],
        "approval_expires_at": action["expires_at"].isoformat(),
    }
    try:
        receipt = submit_order_repair(payload)
    except httpx.RequestError as error:
        with get_connection() as connection:
            connection.execute("UPDATE support.action_executions SET status = 'unknown', error_type = %s, updated_at = NOW() WHERE action_id = %s", (type(error).__name__, action_id))
        save_action_step(action_id, "execute", "unknown", {"error_type": type(error).__name__})
        return show_action(auth, action_id)
    except RuntimeError as error:
        with get_connection() as connection:
            connection.execute("UPDATE support.action_executions SET status = 'failed', error_type = %s, updated_at = NOW() WHERE action_id = %s", (type(error).__name__, action_id))
            connection.execute("UPDATE support.action_proposals SET status = 'blocked', updated_at = NOW() WHERE action_id = %s", (action_id,))
        save_action_step(action_id, "execute", "failed", {"error_type": type(error).__name__})
        return show_action(auth, action_id)
    with get_connection() as connection:
        connection.execute("UPDATE support.action_executions SET status = 'submitted', receipt = %s::jsonb, updated_at = NOW() WHERE action_id = %s", (json.dumps(receipt), action_id))
        connection.execute("UPDATE support.action_proposals SET status = 'awaiting_verification', updated_at = NOW() WHERE action_id = %s", (action_id,))
    receipt_record = save_evidence(str(action["case_id"]), auth.company_id, str(uuid4()), False, None, "RecoverOrderReceipt", {"action_id": action_id, "request_id": request_id}, receipt, "merchant", str(receipt.get("receipt_id") or "") or None, "success", 0, current_trace_id())
    save_action_step(action_id, "execute", "submitted", {"request_id": request_id})
    save_action_step(action_id, "receipt_reconciliation", "accepted", {"duplicate": bool(receipt.get("duplicate")), "task_id": receipt.get("task_id"), "evidence_ids": [receipt_record.evidence_id]}, receipt_record.evidence_id)
    from backend.app.verification import wait_for_order_verification

    return wait_for_order_verification(auth, action_id)


@traceable(name="execute_shipment_recovery", run_type="chain")
def execute_shipment_recovery(auth: AuthContext, action_id: str) -> dict[str, object]:
    if auth.role != "admin":
        raise PermissionError("Only a company admin can execute an approved action")
    action = get_action(auth, action_id)
    if action["action_type"] != "recover_shipment":
        raise ValueError("Action is not a shipment recovery")
    if action["status"] in {"verified_resolved", "verification_failed", "blocked", "rejected", "expired"}:
        return show_action(auth, action_id)
    if action["status"] == "awaiting_verification":
        from backend.app.verification import wait_for_shipment_verification

        return wait_for_shipment_verification(auth, action_id)
    if action["status"] == "executing":
        receipt = reconcile_action_receipt(action)
        if receipt:
            record_reconciled_submission(action, receipt)
            from backend.app.verification import wait_for_shipment_verification

            return wait_for_shipment_verification(auth, action_id)
    if action["status"] not in {"approved", "executing"}:
        raise ValueError("Action is not approved")
    shop_id = str(action["shop_id"])
    order_id = str(action["external_order_id"])
    warehouse = get_shipment(auth, shop_id, order_id)
    merchant = get_shipment_records(auth, shop_id, order_id)
    platform = get_platform_shipment(auth, shop_id, order_id)
    shop = get_shop_status(auth, shop_id)
    source_order = get_order(auth, shop_id, order_id)

    evidence_ids: list[str] = []
    for tool_result in (warehouse, merchant, platform, shop, source_order):
        evidence_id = save_tool_evidence(str(action["case_id"]), auth, tool_result)
        evidence_ids.append(evidence_id)

    snapshot = action["source_snapshot"]
    fields = ("shipment_id", "carrier", "tracking_number")

    warehouse_version_matches = warehouse.response.get("shipment_version") == snapshot.get("version")
    warehouse_fields_match = fields_match(warehouse.response, snapshot, fields)
    warehouse_matches = warehouse.status == "success" and warehouse_version_matches and warehouse_fields_match

    merchant_version_matches = merchant.response.get("version") == snapshot.get("version")
    merchant_fields_match = fields_match(merchant.response, snapshot, fields)
    merchant_matches = merchant.status == "success" and merchant_version_matches and merchant_fields_match

    order_event_matches = source_order.response.get("event_id") == snapshot.get("order_event_id")
    order_version_matches = source_order.response.get("version") == snapshot.get("order_version")
    order_is_paid = source_order.response.get("payment_status") == "paid"
    order_matches = source_order.status == "success" and order_event_matches and order_version_matches and order_is_paid
    if not warehouse_matches or not merchant_matches or not order_matches:
        block_action(action_id, "SHIPMENT_VERSION_CHANGED", evidence_ids[0])
        return show_action(auth, action_id)
    if shop.status != "success" or shop.response.get("version") != action["shop_version"]:
        block_action(action_id, "SHOP_VERSION_CHANGED", evidence_ids[3])
        return show_action(auth, action_id)
    request_id = str(uuid5(NAMESPACE_URL, action["idempotency_key"]))
    with get_connection() as connection:
        decision = connection.execute("SELECT decision_id::text, decided_by FROM support.action_decisions WHERE action_id = %s AND decision = 'approved'", (action_id,)).fetchone()
        if decision is None:
            raise ValueError("Approved decision record is missing")
    claim = claim_action_execution(action_id, request_id)
    if claim == "busy":
        return show_action(auth, action_id)
    if claim == "submitted":
        from backend.app.verification import wait_for_shipment_verification

        return wait_for_shipment_verification(auth, action_id)
    save_action_step(action_id, "scope_version_recheck", "passed", {"source_version": action["source_version"], "shop_version": action["shop_version"], "evidence_ids": evidence_ids}, evidence_ids[0])
    platform_already_matches = platform.status == "success" and fields_match(platform.response, snapshot, fields)
    if platform_already_matches:
        receipt = {"accepted": True, "duplicate": True, "reconciled_from_platform": True}
    else:
        payload = {
            "action_id": action_id,
            "request_id": request_id,
            "company_id": action["company_id"],
            "shop_id": shop_id,
            "external_order_id": order_id,
            "shipment_id": str(action["source_event_id"]),
            "shipment_version": action["source_version"],
            "source_snapshot": snapshot,
            "enable_shipment_sync": action["enable_shipment_sync"],
            "approval_id": decision["decision_id"],
            "approved_by": decision["decided_by"],
            "approval_expires_at": action["expires_at"].isoformat(),
        }
        try:
            receipt = submit_shipment_repair(payload)
        except httpx.RequestError as error:
            with get_connection() as connection:
                connection.execute("UPDATE support.action_executions SET status = 'unknown', error_type = %s, updated_at = NOW() WHERE action_id = %s", (type(error).__name__, action_id))
            save_action_step(action_id, "execute", "unknown", {"error_type": type(error).__name__})
            return show_action(auth, action_id)
        except RuntimeError as error:
            with get_connection() as connection:
                connection.execute("UPDATE support.action_executions SET status = 'failed', error_type = %s, updated_at = NOW() WHERE action_id = %s", (type(error).__name__, action_id))
                connection.execute("UPDATE support.action_proposals SET status = 'blocked', updated_at = NOW() WHERE action_id = %s", (action_id,))
            save_action_step(action_id, "execute", "failed", {"error_type": type(error).__name__})
            return show_action(auth, action_id)
    with get_connection() as connection:
        connection.execute("UPDATE support.action_executions SET status = 'submitted', receipt = %s::jsonb, updated_at = NOW() WHERE action_id = %s", (json.dumps(receipt), action_id))
        connection.execute("UPDATE support.action_proposals SET status = 'awaiting_verification', updated_at = NOW() WHERE action_id = %s", (action_id,))
    receipt_record = save_evidence(str(action["case_id"]), auth.company_id, str(uuid4()), False, None, "RecoverShipmentReceipt", {"action_id": action_id, "request_id": request_id}, receipt, "merchant" if not platform_already_matches else "platform", str(receipt.get("receipt_id") or "") or None, "success", 0, current_trace_id())
    save_action_step(action_id, "execute", "submitted", {"request_id": request_id, "reconciled": platform_already_matches})
    save_action_step(action_id, "receipt_reconciliation", "accepted", {"duplicate": bool(receipt.get("duplicate")), "task_id": receipt.get("task_id"), "evidence_ids": [receipt_record.evidence_id]}, receipt_record.evidence_id)
    from backend.app.verification import wait_for_shipment_verification

    return wait_for_shipment_verification(auth, action_id)


def execute_action(auth: AuthContext, action_id: str) -> dict[str, object]:
    action = get_action(auth, action_id)
    if action["action_type"] == "recover_shipment":
        return execute_shipment_recovery(auth, action_id)
    return execute_order_recovery(auth, action_id)


@traceable(name="decide_order_recovery", run_type="chain")
def decide_action(auth: AuthContext, action_id: str, decision: str) -> dict[str, object]:
    if auth.role != "admin":
        raise PermissionError("Only a company admin can approve or reject an action")
    if decision not in {"approve", "reject"}:
        raise ValueError("Decision must be approve or reject")
    action = get_action(auth, action_id)
    if action["status"] != "proposed":
        if decision == "approve" and action["status"] in {"approved", "executing", "awaiting_verification", "verified_resolved", "verification_failed", "blocked"}:
            if action["status"] in {"approved", "awaiting_verification"}:
                return execute_action(auth, action_id)
            return show_action(auth, action_id)
        return show_action(auth, action_id)
    if action["expires_at"] <= datetime.now(UTC):
        with get_connection() as connection:
            connection.execute("UPDATE support.action_proposals SET status = 'expired', updated_at = NOW() WHERE action_id = %s", (action_id,))
        save_action_step(action_id, "human_approval", "expired", {"expires_at": action["expires_at"]})
        return show_action(auth, action_id)
    if decision == "approve":
        stored_decision = "approved"
    else:
        stored_decision = "rejected"
    with get_connection() as connection:
        connection.execute("INSERT INTO support.action_decisions (decision_id, action_id, decision, decided_by) VALUES (%s, %s, %s, %s)", (str(uuid4()), action_id, stored_decision, auth.user_id))
        connection.execute("UPDATE support.action_proposals SET status = %s, updated_at = NOW() WHERE action_id = %s", (stored_decision, action_id))
    save_action_step(action_id, "human_approval", stored_decision, {"decided_by": auth.user_id})
    if decision == "reject":
        return show_action(auth, action_id)
    return execute_action(auth, action_id)
