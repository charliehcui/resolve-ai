import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
from langsmith import traceable

from app.db import get_connection
from app.evidence import save_evidence
from app.models import AuthContext
from app.support import update_case
from app.tools import ToolResult, execute_tool_batch, get_order, get_shop_status, request_fact
from app.trace import current_trace_id
from services.common import read_service_token

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


def action_idempotency_key(company_id: str, shop_id: str, external_order_id: str, event_id: str, source_version: int, enable_order_sync: bool) -> str:
    value = f"recover_order:{company_id}:{shop_id}:{external_order_id}:{event_id}:{source_version}:{enable_order_sync}"
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
    evidence_by_tool = {record.tool_name: record for record in evidence}
    source = evidence_by_tool["GetOrder"]
    process = evidence_by_tool["GetProcessRecords"]
    shop = evidence_by_tool["GetShopStatus"]
    connection_status = evidence_by_tool["CheckConnection"]
    if source.status != "success":
        raise ValueError("Recoverable source order was not found")
    source_data = source.response
    if source_data.get("payment_status") != "paid":
        raise ValueError("Only paid source orders can be recovered")
    if process.status == "success" and process.response.get("merchant_order_id"):
        update_case(case_id, "diagnosed", "订单已满足目标状态，无需创建恢复方案。", len(evidence), sum(record.latency_ms for record in evidence))
        return {"status": "no_action_needed", "case_id": case_id, "evidence_ids": [record.evidence_id for record in evidence]}
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
    snapshot = {field: source_data[field] for field in snapshot_fields}
    evidence_ids = [record.evidence_id for record in evidence] + [mapping_evidence_id]
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
    result["steps"] = [dict(step) for step in steps]
    return result


@traceable(name="submit_order_repair", run_type="tool")
def submit_order_repair(payload: dict[str, object]) -> dict[str, object]:
    base_url = os.getenv("MERCHANT_URL", "http://127.0.0.1:8002")
    response = httpx.post(f"{base_url}/repairs/orders", json=payload, headers={"X-Service-Token": read_service_token("support-write")}, timeout=5)
    if not response.is_success:
        raise RuntimeError(f"Merchant repair request failed with HTTP {response.status_code}")
    return response.json()


def block_action(action_id: str, reason: str, evidence_id: str | None = None) -> None:
    with get_connection() as connection:
        connection.execute("UPDATE support.action_proposals SET status = 'blocked', updated_at = NOW() WHERE action_id = %s", (action_id,))
    save_action_step(action_id, "scope_version_recheck", "blocked", {"reason": reason, "evidence_ids": [evidence_id] if evidence_id else []}, evidence_id)


@traceable(name="execute_order_recovery", run_type="chain")
def execute_order_recovery(auth: AuthContext, action_id: str) -> dict[str, object]:
    if auth.role != "admin":
        raise PermissionError("Only a company admin can execute an approved action")
    action = get_action(auth, action_id)
    if action["status"] in {"verified_resolved", "verification_failed", "blocked", "rejected", "expired"}:
        return show_action(auth, action_id)
    if action["status"] == "awaiting_verification":
        from app.verify import wait_for_order_verification

        return wait_for_order_verification(auth, action_id)
    if action["status"] != "approved":
        raise ValueError("Action is not approved")
    current_order = get_order(auth, action["shop_id"], action["external_order_id"])
    current_shop = get_shop_status(auth, action["shop_id"])
    order_evidence_id = save_tool_evidence(str(action["case_id"]), auth, current_order)
    shop_evidence_id = save_tool_evidence(str(action["case_id"]), auth, current_shop)
    snapshot = action["source_snapshot"]
    compared_fields = ("event_id", "version", "sku", "quantity", "amount_minor", "payment_status")
    if current_order.status != "success" or any(current_order.response.get(field) != snapshot.get(field) for field in compared_fields):
        block_action(action_id, "SOURCE_VERSION_CHANGED", order_evidence_id)
        return show_action(auth, action_id)
    if current_order.response.get("payment_status") != "paid":
        block_action(action_id, "ORDER_NOT_PAID", order_evidence_id)
        return show_action(auth, action_id)
    if current_shop.status != "success" or current_shop.response.get("version") != action["shop_version"]:
        block_action(action_id, "SHOP_VERSION_CHANGED", shop_evidence_id)
        return show_action(auth, action_id)
    request_id = str(uuid5(NAMESPACE_URL, action["idempotency_key"]))
    claim_until = datetime.now(UTC) + timedelta(seconds=EXECUTION_LEASE_SECONDS)
    with get_connection() as connection:
        decision = connection.execute("SELECT decision_id::text, decided_by FROM support.action_decisions WHERE action_id = %s AND decision = 'approved'", (action_id,)).fetchone()
        if decision is None:
            raise ValueError("Approved decision record is missing")
        execution = connection.execute("SELECT execution_id::text, status FROM support.action_executions WHERE action_id = %s FOR UPDATE", (action_id,)).fetchone()
        if execution is None:
            connection.execute("INSERT INTO support.action_executions (execution_id, action_id, request_id, status, claim_until, trace_id) VALUES (%s, %s, %s, 'claimed', %s, %s)", (str(uuid4()), action_id, request_id, claim_until, current_trace_id()))
        connection.execute("UPDATE support.action_proposals SET status = 'executing', updated_at = NOW() WHERE action_id = %s", (action_id,))
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
    except (httpx.RequestError, RuntimeError) as error:
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
    from app.verify import wait_for_order_verification

    return wait_for_order_verification(auth, action_id)


@traceable(name="decide_order_recovery", run_type="chain")
def decide_action(auth: AuthContext, action_id: str, decision: str) -> dict[str, object]:
    if auth.role != "admin":
        raise PermissionError("Only a company admin can approve or reject an action")
    if decision not in {"approve", "reject"}:
        raise ValueError("Decision must be approve or reject")
    action = get_action(auth, action_id)
    if action["status"] != "proposed":
        if decision == "approve" and action["status"] in {"approved", "executing", "awaiting_verification", "verified_resolved", "verification_failed", "blocked"}:
            return execute_order_recovery(auth, action_id) if action["status"] in {"approved", "awaiting_verification"} else show_action(auth, action_id)
        return show_action(auth, action_id)
    if action["expires_at"] <= datetime.now(UTC):
        with get_connection() as connection:
            connection.execute("UPDATE support.action_proposals SET status = 'expired', updated_at = NOW() WHERE action_id = %s", (action_id,))
        save_action_step(action_id, "human_approval", "expired", {"expires_at": action["expires_at"]})
        return show_action(auth, action_id)
    stored_decision = "approved" if decision == "approve" else "rejected"
    with get_connection() as connection:
        connection.execute("INSERT INTO support.action_decisions (decision_id, action_id, decision, decided_by) VALUES (%s, %s, %s, %s)", (str(uuid4()), action_id, stored_decision, auth.user_id))
        connection.execute("UPDATE support.action_proposals SET status = %s, updated_at = NOW() WHERE action_id = %s", (stored_decision, action_id))
    save_action_step(action_id, "human_approval", stored_decision, {"decided_by": auth.user_id})
    if decision == "reject":
        return show_action(auth, action_id)
    return execute_order_recovery(auth, action_id)
