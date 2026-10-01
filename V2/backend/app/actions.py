import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
from langsmith import traceable

from backend.app.database import get_connection
from backend.app.models import UserContext
from backend.app.support_cases import update_case
from backend.app.support_evidence import EvidenceRecord, save_evidence
from backend.app.support_tools import (
    ReadToolResult,
    call_read_service,
    execute_read_tool_batch,
    get_order,
    get_platform_shipment,
    get_shipment_process_records,
    get_shop_sync_status,
    get_warehouse_shipment,
)
from backend.app.trace import current_trace_id
from simulator.services.common import read_service_token

ACTION_APPROVAL_MINUTES = 10
ACTION_EXECUTION_LEASE_SECONDS = 30


def save_action_step(action_id: str, step_name: str, status: str, details: dict[str, object], evidence_id: str | None = None) -> None:
    with get_connection() as connection:
        connection.execute("INSERT INTO support.action_steps (step_id, action_id, step_name, status, details, evidence_id, trace_id) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)", (str(uuid4()), action_id, step_name, status, json.dumps(details, default=str), evidence_id, current_trace_id()))


def get_recovery_case_context(case_id: str, user: UserContext) -> dict[str, object]:
    with get_connection() as connection:
        case_row = connection.execute("""SELECT c.case_id::text, c.conversation_id::text, c.company_id, h.known_shop_id, h.known_order_id
            FROM support.cases c JOIN support.handoffs h ON h.handoff_id = c.handoff_id
            JOIN support.conversations v ON v.conversation_id = c.conversation_id
            WHERE c.case_id = %s AND c.company_id = %s AND v.user_id = %s""", (case_id, user.company_id, user.user_id)).fetchone()

    if case_row is None:
        raise PermissionError("Support case is not available in this user scope")

    if case_row["known_shop_id"] is None or case_row["known_order_id"] is None:
        raise ValueError("Case requires known shop_id and order_id")

    return dict(case_row)


def get_sku_mapping(user: UserContext, shop_id: str, platform_sku: str) -> ReadToolResult:
    merchant_url = os.getenv("MERCHANT_URL", "http://127.0.0.1:8002")
    sku_mapping_result = call_read_service("GetSkuMapping", "merchant", f"{merchant_url}/internal/shops/{shop_id}/mappings/{platform_sku}", user.company_id, {})
    sku_mapping_result.request = {"shop_id": shop_id, "platform_sku": platform_sku}

    return sku_mapping_result


def save_read_tool_evidence(case_id: str, user: UserContext, tool_result: ReadToolResult) -> str:
    evidence_record = save_evidence(case_id, user.company_id, str(uuid4()), False, None, tool_result.tool_name, tool_result.request, tool_result.response, tool_result.source_service, tool_result.source_record_id, tool_result.status, tool_result.latency_ms, tool_result.trace_id)

    return evidence_record.evidence_id


def index_evidence_by_tool(records: list[EvidenceRecord]) -> dict[str, EvidenceRecord]:
    evidence_by_tool_name: dict[str, EvidenceRecord] = {}

    for record in records:
        evidence_by_tool_name[record.tool_name] = record

    return evidence_by_tool_name


def get_evidence_ids(records: list[EvidenceRecord]) -> list[str]:
    evidence_ids: list[str] = []

    for record in records:
        evidence_ids.append(record.evidence_id)

    return evidence_ids


def get_total_evidence_latency_ms(records: list[EvidenceRecord]) -> int:
    total_latency_ms = 0

    for record in records:
        total_latency_ms += record.latency_ms

    return total_latency_ms


def fields_match(actual_values: dict[str, object], expected_values: dict[str, object], field_names: tuple[str, ...]) -> bool:
    for field_name in field_names:
        if actual_values.get(field_name) != expected_values.get(field_name):
            return False

    return True


def build_order_recovery_idempotency_key(company_id: str, shop_id: str, external_order_id: str, event_id: str, source_version: int, enable_order_sync: bool) -> str:
    key_text = f"recover_order:{company_id}:{shop_id}:{external_order_id}:{event_id}:{source_version}:{enable_order_sync}"

    return hashlib.sha256(key_text.encode("utf-8")).hexdigest()


def build_shipment_recovery_idempotency_key(company_id: str, shop_id: str, external_order_id: str, shipment_id: str, source_version: int, enable_shipment_sync: bool) -> str:
    key_text = f"recover_shipment:{company_id}:{shop_id}:{external_order_id}:{shipment_id}:{source_version}:{enable_shipment_sync}"

    return hashlib.sha256(key_text.encode("utf-8")).hexdigest()


@traceable(name="propose_order_recovery", run_type="chain")
def propose_order_recovery(user: UserContext, case_id: str, enable_order_sync: bool = False) -> dict[str, object]:
    case_context = get_recovery_case_context(case_id, user)
    shop_id = str(case_context["known_shop_id"])
    order_id = str(case_context["known_order_id"])

    read_tool_calls = [
        {"name": "GetOrder", "args": {"shop_id": shop_id, "order_id": order_id}, "id": "proposal-order"},
        {"name": "GetOrderProcessRecords", "args": {"shop_id": shop_id, "order_id": order_id}, "id": "proposal-process"},
        {"name": "GetShopSyncStatus", "args": {"shop_id": shop_id}, "id": "proposal-shop"},
        {"name": "GetShopConnectionStatus", "args": {"shop_id": shop_id}, "id": "proposal-connection"},
    ]

    evidence = execute_read_tool_batch(case_id, user, read_tool_calls, shop_id, order_id)
    evidence_by_tool_name = index_evidence_by_tool(evidence)

    source_order = evidence_by_tool_name["GetOrder"]
    order_process = evidence_by_tool_name["GetOrderProcessRecords"]
    shop_sync_status = evidence_by_tool_name["GetShopSyncStatus"]
    shop_connection_status = evidence_by_tool_name["GetShopConnectionStatus"]

    if source_order.status != "success":
        raise ValueError("Recoverable source order was not found")

    source_order_data = source_order.response

    if source_order_data.get("payment_status") != "paid":
        raise ValueError("Only paid source orders can be recovered")

    if order_process.status == "success" and order_process.response.get("merchant_order_id") is not None:
        update_case(case_id, "diagnosed", "订单已满足目标状态，无需创建恢复方案。", len(evidence), get_total_evidence_latency_ms(evidence))
        return {"status": "no_action_needed", "case_id": case_id, "evidence_ids": get_evidence_ids(evidence)}

    if shop_sync_status.status != "success" or shop_connection_status.status != "success":
        raise ValueError("Shop state could not be verified")

    if shop_sync_status.response.get("sync_enabled") is not True and enable_order_sync is False:
        raise ValueError("Order sync is disabled; enabling it requires an explicit proposal option")

    if shop_connection_status.response.get("channel") == "B" and shop_connection_status.response.get("connection_status") != "authorized":
        raise ValueError("Shop authorization cannot be repaired by recover_order")

    sku_mapping = get_sku_mapping(user, shop_id, str(source_order_data["sku"]))
    sku_mapping_evidence_id = save_read_tool_evidence(case_id, user, sku_mapping)

    if sku_mapping.status != "success" or sku_mapping.response.get("active") is not True:
        raise ValueError("SKU mapping must exist before order recovery")

    snapshot_fields = ("event_id", "version", "sku", "quantity", "amount_minor", "payment_status")
    source_snapshot: dict[str, object] = {}

    for field_name in snapshot_fields:
        source_snapshot[field_name] = source_order_data[field_name]

    evidence_ids = get_evidence_ids(evidence)
    evidence_ids.append(sku_mapping_evidence_id)

    idempotency_key = build_order_recovery_idempotency_key(user.company_id, shop_id, order_id, str(source_order_data["event_id"]), int(source_order_data["version"]), enable_order_sync)
    action_id = str(uuid4())
    approval_expires_at = datetime.now(UTC) + timedelta(minutes=ACTION_APPROVAL_MINUTES)

    with get_connection() as connection:
        existing_action = connection.execute("SELECT action_id::text FROM support.action_proposals WHERE idempotency_key = %s AND company_id = %s", (idempotency_key, user.company_id)).fetchone()

        if existing_action is not None:
            return get_action_details(user, existing_action["action_id"])

        connection.execute("""INSERT INTO support.action_proposals (action_id, case_id, company_id, shop_id, external_order_id, action_type, status, source_event_id, source_version, shop_version, source_snapshot, enable_order_sync, evidence_ids, idempotency_key, proposed_by, expires_at)
            VALUES (%s, %s, %s, %s, %s, 'recover_order', 'proposed', %s, %s, %s, %s::jsonb, %s, %s::jsonb, %s, %s, %s)""", (action_id, case_id, user.company_id, shop_id, order_id, source_order_data["event_id"], source_order_data["version"], shop_sync_status.response["version"], json.dumps(source_snapshot), enable_order_sync, json.dumps(evidence_ids), idempotency_key, user.user_id, approval_expires_at))

    save_action_step(action_id, "action_proposal", "passed", {"action_type": "recover_order", "shop_id": shop_id, "order_id": order_id, "enable_order_sync": enable_order_sync, "evidence_ids": evidence_ids}, source_order.evidence_id)
    save_action_step(action_id, "policy_check", "passed", {"payment_status": "paid", "mapping_active": True, "connection_authorized": True, "source_version": source_order_data["version"], "shop_version": shop_sync_status.response["version"], "evidence_ids": evidence_ids}, sku_mapping_evidence_id)

    return get_action_details(user, action_id)


@traceable(name="propose_shipment_recovery", run_type="chain")
def propose_shipment_recovery(user: UserContext, case_id: str, enable_shipment_sync: bool = False) -> dict[str, object]:
    case_context = get_recovery_case_context(case_id, user)
    shop_id = str(case_context["known_shop_id"])
    order_id = str(case_context["known_order_id"])

    read_tool_calls = [
        {"name": "GetOrder", "args": {"shop_id": shop_id, "order_id": order_id}, "id": "proposal-source-order"},
        {"name": "GetWarehouseShipment", "args": {"shop_id": shop_id, "order_id": order_id}, "id": "proposal-warehouse-shipment"},
        {"name": "GetShipmentProcessRecords", "args": {"shop_id": shop_id, "order_id": order_id}, "id": "proposal-merchant-shipment"},
        {"name": "GetPlatformShipment", "args": {"shop_id": shop_id, "order_id": order_id}, "id": "proposal-platform-shipment"},
        {"name": "GetShopSyncStatus", "args": {"shop_id": shop_id}, "id": "proposal-shop"},
    ]

    evidence = execute_read_tool_batch(case_id, user, read_tool_calls, shop_id, order_id)
    evidence_by_tool_name = index_evidence_by_tool(evidence)

    source_order = evidence_by_tool_name["GetOrder"]
    warehouse_shipment = evidence_by_tool_name["GetWarehouseShipment"]
    shipment_process = evidence_by_tool_name["GetShipmentProcessRecords"]
    platform_shipment = evidence_by_tool_name["GetPlatformShipment"]
    shop_sync_status = evidence_by_tool_name["GetShopSyncStatus"]

    if source_order.status != "success" or source_order.response.get("payment_status") != "paid":
        raise ValueError("Only a currently paid source order can receive shipment recovery")

    warehouse_shipment_id = warehouse_shipment.response.get("shipment_id")

    if warehouse_shipment.status != "success" or warehouse_shipment_id is None or warehouse_shipment.response.get("shipment_count") != 1:
        raise ValueError("Exactly one warehouse shipment fact is required")

    if shipment_process.status != "success" or shipment_process.response.get("shipment_id") != warehouse_shipment_id:
        raise ValueError("Merchant must hold the matching warehouse shipment before recovery")

    expected_shipment = {"shipment_id": warehouse_shipment.response["shipment_id"], "carrier": warehouse_shipment.response["carrier"], "tracking_number": warehouse_shipment.response["tracking_number"]}
    shipment_fields = ("shipment_id", "carrier", "tracking_number")

    if platform_shipment.status == "success" and fields_match(platform_shipment.response, expected_shipment, shipment_fields):
        update_case(case_id, "diagnosed", "平台发货状态已满足目标，无需创建恢复方案。", len(evidence), get_total_evidence_latency_ms(evidence))
        return {"status": "no_action_needed", "case_id": case_id, "evidence_ids": get_evidence_ids(evidence)}

    if platform_shipment.status in {"not_found", "empty"}:
        pass
    else:
        raise ValueError("Platform shipment state conflicts with the warehouse fact")

    if shop_sync_status.status != "success":
        raise ValueError("Shop state could not be verified")

    if shop_sync_status.response.get("shipment_sync_enabled") is not True and enable_shipment_sync is False:
        raise ValueError("Shipment sync is disabled; enabling it requires an explicit proposal option")

    source_snapshot = dict(expected_shipment)
    source_snapshot["version"] = warehouse_shipment.response["shipment_version"]
    source_snapshot["shipped_at"] = warehouse_shipment.response["shipped_at"]
    source_snapshot["order_event_id"] = source_order.response["event_id"]
    source_snapshot["order_version"] = source_order.response["version"]
    source_snapshot["payment_status"] = source_order.response["payment_status"]

    evidence_ids = get_evidence_ids(evidence)
    idempotency_key = build_shipment_recovery_idempotency_key(user.company_id, shop_id, order_id, str(source_snapshot["shipment_id"]), int(source_snapshot["version"]), enable_shipment_sync)
    action_id = str(uuid4())
    approval_expires_at = datetime.now(UTC) + timedelta(minutes=ACTION_APPROVAL_MINUTES)

    with get_connection() as connection:
        existing_action = connection.execute("SELECT action_id::text FROM support.action_proposals WHERE idempotency_key = %s AND company_id = %s", (idempotency_key, user.company_id)).fetchone()

        if existing_action is not None:
            return get_action_details(user, existing_action["action_id"])

        connection.execute("""INSERT INTO support.action_proposals
            (action_id, case_id, company_id, shop_id, external_order_id, action_type, status, source_event_id, source_version, shop_version, source_snapshot, enable_shipment_sync, evidence_ids, idempotency_key, proposed_by, expires_at)
            VALUES (%s, %s, %s, %s, %s, 'recover_shipment', 'proposed', %s, %s, %s, %s::jsonb, %s, %s::jsonb, %s, %s, %s)""", (action_id, case_id, user.company_id, shop_id, order_id, source_snapshot["shipment_id"], source_snapshot["version"], shop_sync_status.response["version"], json.dumps(source_snapshot, default=str), enable_shipment_sync, json.dumps(evidence_ids), idempotency_key, user.user_id, approval_expires_at))

    recovery_steps: list[str] = []

    if enable_shipment_sync is True:
        recovery_steps.append("enable_shipment_sync")

    recovery_steps.append("resend_specific_shipment")

    save_action_step(action_id, "action_proposal", "passed", {"action_type": "recover_shipment", "shop_id": shop_id, "order_id": order_id, "steps": recovery_steps, "evidence_ids": evidence_ids}, warehouse_shipment.evidence_id)
    save_action_step(action_id, "policy_check", "passed", {"warehouse_shipment_count": 1, "merchant_matches": True, "source_version": source_snapshot["version"], "shop_version": shop_sync_status.response["version"], "evidence_ids": evidence_ids}, shipment_process.evidence_id)

    return get_action_details(user, action_id)


def get_action_proposal(user: UserContext, action_id: str) -> dict[str, object]:
    with get_connection() as connection:
        action_row = connection.execute("""SELECT action_id, case_id, company_id, shop_id, external_order_id, action_type, status, source_event_id, source_version, shop_version, source_snapshot, enable_order_sync, enable_shipment_sync, evidence_ids, idempotency_key, proposed_by, expires_at, created_at, updated_at
            FROM support.action_proposals WHERE action_id = %s AND company_id = %s""", (action_id, user.company_id)).fetchone()

    if action_row is None:
        raise PermissionError("Action is not available in this company scope")

    return dict(action_row)


def get_action_details(user: UserContext, action_id: str) -> dict[str, object]:
    action_proposal = get_action_proposal(user, action_id)

    with get_connection() as connection:
        decision_row = connection.execute("SELECT decision_id::text, decision, decided_by, decided_at FROM support.action_decisions WHERE action_id = %s", (action_id,)).fetchone()
        execution_row = connection.execute("SELECT execution_id::text, request_id::text, status, claim_until, attempts, receipt, error_type, trace_id, created_at, updated_at FROM support.action_executions WHERE action_id = %s", (action_id,)).fetchone()
        verification_row = connection.execute("SELECT verification_id::text, status, details, evidence_ids, trace_id, checked_at FROM support.action_verifications WHERE action_id = %s", (action_id,)).fetchone()
        step_rows = connection.execute("SELECT step_id::text, step_name, status, details, evidence_id::text, trace_id, created_at FROM support.action_steps WHERE action_id = %s ORDER BY created_at", (action_id,)).fetchall()

    action_details = dict(action_proposal)
    action_details["action_id"] = str(action_details["action_id"])
    action_details["case_id"] = str(action_details["case_id"])
    action_details["source_event_id"] = str(action_details["source_event_id"])

    if decision_row is None:
        action_details["decision"] = None
    else:
        action_details["decision"] = dict(decision_row)

    if execution_row is None:
        action_details["execution"] = None
    else:
        action_details["execution"] = dict(execution_row)

    if verification_row is None:
        action_details["verification"] = None
    else:
        action_details["verification"] = dict(verification_row)

    action_steps: list[dict[str, object]] = []

    for step_row in step_rows:
        action_steps.append(dict(step_row))

    action_details["steps"] = action_steps

    return action_details


@traceable(name="submit_order_repair", run_type="tool")
def submit_order_repair(payload: dict[str, object]) -> dict[str, object]:
    merchant_url = os.getenv("MERCHANT_URL", "http://127.0.0.1:8002")
    response = httpx.post(f"{merchant_url}/repairs/orders", json=payload, headers={"X-Service-Token": read_service_token("support-write")}, timeout=5)

    if response.is_success is False:
        raise RuntimeError(f"Merchant repair request failed with HTTP {response.status_code}")

    return response.json()


@traceable(name="submit_shipment_repair", run_type="tool")
def submit_shipment_repair(payload: dict[str, object]) -> dict[str, object]:
    merchant_url = os.getenv("MERCHANT_URL", "http://127.0.0.1:8002")
    response = httpx.post(f"{merchant_url}/repairs/shipments", json=payload, headers={"X-Service-Token": read_service_token("support-write")}, timeout=5)

    if response.is_success is False:
        raise RuntimeError(f"Merchant shipment repair request failed with HTTP {response.status_code}")

    return response.json()


def mark_action_blocked(action_id: str, reason: str, evidence_id: str | None = None) -> None:
    with get_connection() as connection:
        connection.execute("UPDATE support.action_proposals SET status = 'blocked', updated_at = NOW() WHERE action_id = %s", (action_id,))

    evidence_ids: list[str] = []

    if evidence_id is not None and evidence_id != "":
        evidence_ids.append(evidence_id)

    save_action_step(action_id, "scope_version_recheck", "blocked", {"reason": reason, "evidence_ids": evidence_ids}, evidence_id)


def acquire_action_execution(action_id: str, request_id: str) -> str:
    current_time = datetime.now(UTC)
    execution_lease_until = current_time + timedelta(seconds=ACTION_EXECUTION_LEASE_SECONDS)

    with get_connection() as connection:
        approval_row = connection.execute("""SELECT 1 FROM support.action_proposals p JOIN support.action_decisions d ON d.action_id = p.action_id
            WHERE p.action_id = %s AND p.status IN ('approved', 'executing') AND d.decision = 'approved'""", (action_id,)).fetchone()

        if approval_row is None:
            raise ValueError("Action does not have a valid approval")

        execution_row = connection.execute("SELECT execution_id::text, status, claim_until FROM support.action_executions WHERE action_id = %s FOR UPDATE", (action_id,)).fetchone()

        if execution_row is None:
            connection.execute("INSERT INTO support.action_executions (execution_id, action_id, request_id, status, claim_until, trace_id) VALUES (%s, %s, %s, 'claimed', %s, %s)", (str(uuid4()), action_id, request_id, execution_lease_until, current_trace_id()))
        elif execution_row["status"] == "submitted":
            return "submitted"
        elif execution_row["status"] == "claimed" and execution_row["claim_until"] > current_time:
            return "busy"
        else:
            connection.execute("UPDATE support.action_executions SET status = 'claimed', claim_until = %s, attempts = attempts + 1, error_type = NULL, trace_id = %s, updated_at = NOW() WHERE action_id = %s", (execution_lease_until, current_trace_id(), action_id))

        connection.execute("UPDATE support.action_proposals SET status = 'executing', updated_at = NOW() WHERE action_id = %s", (action_id,))

    return "acquired"


def get_existing_recovery_receipt(action_proposal: dict[str, object]) -> dict[str, object] | None:
    merchant_url = os.getenv("MERCHANT_URL", "http://127.0.0.1:8002")

    if action_proposal["action_type"] == "recover_shipment":
        resource_name = "shipments"
    else:
        resource_name = "orders"

    receipt_result = call_read_service("GetRecoveryReceipt", "merchant", f"{merchant_url}/repairs/{resource_name}/{action_proposal['action_id']}", str(action_proposal["company_id"]), {})

    if receipt_result.status == "success":
        return receipt_result.response

    return None


def record_existing_submission(action_proposal: dict[str, object], receipt: dict[str, object]) -> None:
    with get_connection() as connection:
        connection.execute("UPDATE support.action_executions SET status = 'submitted', receipt = %s::jsonb, updated_at = NOW() WHERE action_id = %s", (json.dumps(receipt, default=str), action_proposal["action_id"]))
        connection.execute("UPDATE support.action_proposals SET status = 'awaiting_verification', updated_at = NOW() WHERE action_id = %s", (action_proposal["action_id"],))

    save_action_step(str(action_proposal["action_id"]), "receipt_reconciliation", "accepted", {"recovered_after_unknown_result": True, "task_id": receipt.get("task_id")})


@traceable(name="execute_order_recovery", run_type="chain")
def execute_order_recovery(user: UserContext, action_id: str) -> dict[str, object]:
    if user.role != "admin":
        raise PermissionError("Only a company admin can execute an approved action")

    action_proposal = get_action_proposal(user, action_id)

    if action_proposal["status"] in {"verified_resolved", "verification_failed", "blocked", "rejected", "expired"}:
        return get_action_details(user, action_id)

    if action_proposal["status"] == "awaiting_verification":
        from backend.app.verification import wait_for_order_verification
        return wait_for_order_verification(user, action_id)

    if action_proposal["status"] == "executing":
        existing_receipt = get_existing_recovery_receipt(action_proposal)

        if existing_receipt is not None:
            record_existing_submission(action_proposal, existing_receipt)

            from backend.app.verification import wait_for_order_verification
            return wait_for_order_verification(user, action_id)

    if action_proposal["status"] in {"approved", "executing"}:
        pass
    else:
        raise ValueError("Action is not approved")

    current_order = get_order(user, action_proposal["shop_id"], action_proposal["external_order_id"])
    current_shop_sync_status = get_shop_sync_status(user, action_proposal["shop_id"])

    order_evidence_id = save_read_tool_evidence(str(action_proposal["case_id"]), user, current_order)
    shop_evidence_id = save_read_tool_evidence(str(action_proposal["case_id"]), user, current_shop_sync_status)

    source_snapshot = action_proposal["source_snapshot"]
    compared_fields = ("event_id", "version", "sku", "quantity", "amount_minor", "payment_status")
    source_order_matches = current_order.status == "success" and fields_match(current_order.response, source_snapshot, compared_fields)

    if source_order_matches is False:
        mark_action_blocked(action_id, "SOURCE_VERSION_CHANGED", order_evidence_id)
        return get_action_details(user, action_id)

    if current_order.response.get("payment_status") != "paid":
        mark_action_blocked(action_id, "ORDER_NOT_PAID", order_evidence_id)
        return get_action_details(user, action_id)

    if current_shop_sync_status.status != "success" or current_shop_sync_status.response.get("version") != action_proposal["shop_version"]:
        mark_action_blocked(action_id, "SHOP_VERSION_CHANGED", shop_evidence_id)
        return get_action_details(user, action_id)

    request_id = str(uuid5(NAMESPACE_URL, action_proposal["idempotency_key"]))

    with get_connection() as connection:
        approval_decision = connection.execute("SELECT decision_id::text, decided_by FROM support.action_decisions WHERE action_id = %s AND decision = 'approved'", (action_id,)).fetchone()

        if approval_decision is None:
            raise ValueError("Approved decision record is missing")

    execution_status = acquire_action_execution(action_id, request_id)

    if execution_status == "busy":
        return get_action_details(user, action_id)

    if execution_status == "submitted":
        from backend.app.verification import wait_for_order_verification
        return wait_for_order_verification(user, action_id)

    save_action_step(action_id, "scope_version_recheck", "passed", {"source_version": action_proposal["source_version"], "shop_version": action_proposal["shop_version"], "evidence_ids": [order_evidence_id, shop_evidence_id]}, order_evidence_id)

    repair_payload = {
        "action_id": action_id,
        "request_id": request_id,
        "company_id": action_proposal["company_id"],
        "shop_id": action_proposal["shop_id"],
        "external_order_id": action_proposal["external_order_id"],
        "source_event_id": str(action_proposal["source_event_id"]),
        "source_version": action_proposal["source_version"],
        "shop_version": action_proposal["shop_version"],
        "source_snapshot": source_snapshot,
        "enable_order_sync": action_proposal["enable_order_sync"],
        "approval_id": approval_decision["decision_id"],
        "approved_by": approval_decision["decided_by"],
        "approval_expires_at": action_proposal["expires_at"].isoformat(),
    }

    try:
        receipt = submit_order_repair(repair_payload)
    except httpx.RequestError as error:
        with get_connection() as connection:
            connection.execute("UPDATE support.action_executions SET status = 'unknown', error_type = %s, updated_at = NOW() WHERE action_id = %s", (type(error).__name__, action_id))

        save_action_step(action_id, "execute", "unknown", {"error_type": type(error).__name__})
        return get_action_details(user, action_id)

    except RuntimeError as error:
        with get_connection() as connection:
            connection.execute("UPDATE support.action_executions SET status = 'failed', error_type = %s, updated_at = NOW() WHERE action_id = %s", (type(error).__name__, action_id))
            connection.execute("UPDATE support.action_proposals SET status = 'blocked', updated_at = NOW() WHERE action_id = %s", (action_id,))

        save_action_step(action_id, "execute", "failed", {"error_type": type(error).__name__})
        return get_action_details(user, action_id)

    with get_connection() as connection:
        connection.execute("UPDATE support.action_executions SET status = 'submitted', receipt = %s::jsonb, updated_at = NOW() WHERE action_id = %s", (json.dumps(receipt), action_id))
        connection.execute("UPDATE support.action_proposals SET status = 'awaiting_verification', updated_at = NOW() WHERE action_id = %s", (action_id,))

    receipt_id_value = receipt.get("receipt_id")

    if receipt_id_value is None or str(receipt_id_value) == "":
        receipt_id = None
    else:
        receipt_id = str(receipt_id_value)

    receipt_record = save_evidence(str(action_proposal["case_id"]), user.company_id, str(uuid4()), False, None, "RecoverOrderReceipt", {"action_id": action_id, "request_id": request_id}, receipt, "merchant", receipt_id, "success", 0, current_trace_id())

    save_action_step(action_id, "execute", "submitted", {"request_id": request_id})
    save_action_step(action_id, "receipt_reconciliation", "accepted", {"duplicate": bool(receipt.get("duplicate")), "task_id": receipt.get("task_id"), "evidence_ids": [receipt_record.evidence_id]}, receipt_record.evidence_id)

    from backend.app.verification import wait_for_order_verification

    return wait_for_order_verification(user, action_id)


@traceable(name="execute_shipment_recovery", run_type="chain")
def execute_shipment_recovery(user: UserContext, action_id: str) -> dict[str, object]:
    if user.role != "admin":
        raise PermissionError("Only a company admin can execute an approved action")

    action_proposal = get_action_proposal(user, action_id)

    if action_proposal["action_type"] != "recover_shipment":
        raise ValueError("Action is not a shipment recovery")

    if action_proposal["status"] in {"verified_resolved", "verification_failed", "blocked", "rejected", "expired"}:
        return get_action_details(user, action_id)

    if action_proposal["status"] == "awaiting_verification":
        from backend.app.verification import wait_for_shipment_verification
        return wait_for_shipment_verification(user, action_id)

    if action_proposal["status"] == "executing":
        existing_receipt = get_existing_recovery_receipt(action_proposal)

        if existing_receipt is not None:
            record_existing_submission(action_proposal, existing_receipt)

            from backend.app.verification import wait_for_shipment_verification
            return wait_for_shipment_verification(user, action_id)

    if action_proposal["status"] in {"approved", "executing"}:
        pass
    else:
        raise ValueError("Action is not approved")

    shop_id = str(action_proposal["shop_id"])
    order_id = str(action_proposal["external_order_id"])

    warehouse_shipment = get_warehouse_shipment(user, shop_id, order_id)
    shipment_process = get_shipment_process_records(user, shop_id, order_id)
    platform_shipment = get_platform_shipment(user, shop_id, order_id)
    shop_sync_status = get_shop_sync_status(user, shop_id)
    source_order = get_order(user, shop_id, order_id)

    evidence_ids: list[str] = []

    for tool_result in (warehouse_shipment, shipment_process, platform_shipment, shop_sync_status, source_order):
        evidence_id = save_read_tool_evidence(str(action_proposal["case_id"]), user, tool_result)
        evidence_ids.append(evidence_id)

    source_snapshot = action_proposal["source_snapshot"]
    shipment_fields = ("shipment_id", "carrier", "tracking_number")

    warehouse_version_matches = warehouse_shipment.response.get("shipment_version") == source_snapshot.get("version")
    warehouse_fields_match = fields_match(warehouse_shipment.response, source_snapshot, shipment_fields)
    warehouse_matches = warehouse_shipment.status == "success" and warehouse_version_matches and warehouse_fields_match

    shipment_process_version_matches = shipment_process.response.get("version") == source_snapshot.get("version")
    shipment_process_fields_match = fields_match(shipment_process.response, source_snapshot, shipment_fields)
    shipment_process_matches = shipment_process.status == "success" and shipment_process_version_matches and shipment_process_fields_match

    order_event_matches = source_order.response.get("event_id") == source_snapshot.get("order_event_id")
    order_version_matches = source_order.response.get("version") == source_snapshot.get("order_version")
    order_is_paid = source_order.response.get("payment_status") == "paid"
    source_order_matches = source_order.status == "success" and order_event_matches and order_version_matches and order_is_paid

    if warehouse_matches is False or shipment_process_matches is False or source_order_matches is False:
        mark_action_blocked(action_id, "SHIPMENT_VERSION_CHANGED", evidence_ids[0])
        return get_action_details(user, action_id)

    if shop_sync_status.status != "success" or shop_sync_status.response.get("version") != action_proposal["shop_version"]:
        mark_action_blocked(action_id, "SHOP_VERSION_CHANGED", evidence_ids[3])
        return get_action_details(user, action_id)

    request_id = str(uuid5(NAMESPACE_URL, action_proposal["idempotency_key"]))

    with get_connection() as connection:
        approval_decision = connection.execute("SELECT decision_id::text, decided_by FROM support.action_decisions WHERE action_id = %s AND decision = 'approved'", (action_id,)).fetchone()

        if approval_decision is None:
            raise ValueError("Approved decision record is missing")

    execution_status = acquire_action_execution(action_id, request_id)

    if execution_status == "busy":
        return get_action_details(user, action_id)

    if execution_status == "submitted":
        from backend.app.verification import wait_for_shipment_verification
        return wait_for_shipment_verification(user, action_id)

    save_action_step(action_id, "scope_version_recheck", "passed", {"source_version": action_proposal["source_version"], "shop_version": action_proposal["shop_version"], "evidence_ids": evidence_ids}, evidence_ids[0])

    platform_already_matches = platform_shipment.status == "success" and fields_match(platform_shipment.response, source_snapshot, shipment_fields)

    if platform_already_matches is True:
        receipt = {"accepted": True, "duplicate": True, "reconciled_from_platform": True}
        receipt_source_service = "platform"
    else:
        repair_payload = {
            "action_id": action_id,
            "request_id": request_id,
            "company_id": action_proposal["company_id"],
            "shop_id": shop_id,
            "external_order_id": order_id,
            "shipment_id": str(action_proposal["source_event_id"]),
            "shipment_version": action_proposal["source_version"],
            "source_snapshot": source_snapshot,
            "enable_shipment_sync": action_proposal["enable_shipment_sync"],
            "approval_id": approval_decision["decision_id"],
            "approved_by": approval_decision["decided_by"],
            "approval_expires_at": action_proposal["expires_at"].isoformat(),
        }

        try:
            receipt = submit_shipment_repair(repair_payload)
        except httpx.RequestError as error:
            with get_connection() as connection:
                connection.execute("UPDATE support.action_executions SET status = 'unknown', error_type = %s, updated_at = NOW() WHERE action_id = %s", (type(error).__name__, action_id))

            save_action_step(action_id, "execute", "unknown", {"error_type": type(error).__name__})
            return get_action_details(user, action_id)

        except RuntimeError as error:
            with get_connection() as connection:
                connection.execute("UPDATE support.action_executions SET status = 'failed', error_type = %s, updated_at = NOW() WHERE action_id = %s", (type(error).__name__, action_id))
                connection.execute("UPDATE support.action_proposals SET status = 'blocked', updated_at = NOW() WHERE action_id = %s", (action_id,))

            save_action_step(action_id, "execute", "failed", {"error_type": type(error).__name__})
            return get_action_details(user, action_id)

        receipt_source_service = "merchant"

    with get_connection() as connection:
        connection.execute("UPDATE support.action_executions SET status = 'submitted', receipt = %s::jsonb, updated_at = NOW() WHERE action_id = %s", (json.dumps(receipt), action_id))
        connection.execute("UPDATE support.action_proposals SET status = 'awaiting_verification', updated_at = NOW() WHERE action_id = %s", (action_id,))

    receipt_id_value = receipt.get("receipt_id")

    if receipt_id_value is None or str(receipt_id_value) == "":
        receipt_id = None
    else:
        receipt_id = str(receipt_id_value)

    receipt_record = save_evidence(str(action_proposal["case_id"]), user.company_id, str(uuid4()), False, None, "RecoverShipmentReceipt", {"action_id": action_id, "request_id": request_id}, receipt, receipt_source_service, receipt_id, "success", 0, current_trace_id())

    save_action_step(action_id, "execute", "submitted", {"request_id": request_id, "reconciled": platform_already_matches})
    save_action_step(action_id, "receipt_reconciliation", "accepted", {"duplicate": bool(receipt.get("duplicate")), "task_id": receipt.get("task_id"), "evidence_ids": [receipt_record.evidence_id]}, receipt_record.evidence_id)

    from backend.app.verification import wait_for_shipment_verification

    return wait_for_shipment_verification(user, action_id)


def execute_recovery_action(user: UserContext, action_id: str) -> dict[str, object]:
    action_proposal = get_action_proposal(user, action_id)

    if action_proposal["action_type"] == "recover_shipment":
        return execute_shipment_recovery(user, action_id)

    return execute_order_recovery(user, action_id)


@traceable(name="decide_recovery_action", run_type="chain")
def decide_recovery_action(user: UserContext, action_id: str, decision: str) -> dict[str, object]:
    if user.role != "admin":
        raise PermissionError("Only a company admin can approve or reject an action")

    if decision in {"approve", "reject"}:
        pass
    else:
        raise ValueError("Decision must be approve or reject")

    action_proposal = get_action_proposal(user, action_id)

    if action_proposal["status"] != "proposed":
        if decision == "approve" and action_proposal["status"] in {"approved", "executing", "awaiting_verification", "verified_resolved", "verification_failed", "blocked"}:
            if action_proposal["status"] in {"approved", "awaiting_verification"}:
                return execute_recovery_action(user, action_id)

            return get_action_details(user, action_id)

        return get_action_details(user, action_id)

    if action_proposal["expires_at"] <= datetime.now(UTC):
        with get_connection() as connection:
            connection.execute("UPDATE support.action_proposals SET status = 'expired', updated_at = NOW() WHERE action_id = %s", (action_id,))

        save_action_step(action_id, "human_approval", "expired", {"expires_at": action_proposal["expires_at"]})
        return get_action_details(user, action_id)

    if decision == "approve":
        stored_decision = "approved"
    else:
        stored_decision = "rejected"

    with get_connection() as connection:
        connection.execute("INSERT INTO support.action_decisions (decision_id, action_id, decision, decided_by) VALUES (%s, %s, %s, %s)", (str(uuid4()), action_id, stored_decision, user.user_id))
        connection.execute("UPDATE support.action_proposals SET status = %s, updated_at = NOW() WHERE action_id = %s", (stored_decision, action_id))

    save_action_step(action_id, "human_approval", stored_decision, {"decided_by": user.user_id})

    if decision == "reject":
        return get_action_details(user, action_id)

    return execute_recovery_action(user, action_id)
