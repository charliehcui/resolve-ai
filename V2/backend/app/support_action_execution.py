import json
import os
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
from langsmith import traceable

from backend.app.database import get_connection
from backend.app.models import UserContext
from backend.app.support_action_approvals import authorize_action_decision
from backend.app.support_action_registry import action_policy
from backend.app.support_action_store import fields_match, get_action_details, get_action_plan, save_action_step, save_read_tool_evidence
from backend.app.support_evidence import save_evidence
from backend.app.support_tools import call_read_service, get_order, get_platform_shipment, get_shipment_process_records, get_shop_sync_status, get_warehouse_shipment
from backend.app.trace import current_trace_id
from simulator.services.common import read_service_token

ACTION_EXECUTION_LEASE_SECONDS = 30


def submit_background_repair(resource: str, payload: dict[str, object]) -> dict[str, object]:
    merchant_url = os.getenv("MERCHANT_URL", "http://127.0.0.1:8002")
    response = httpx.post(f"{merchant_url}/repairs/{resource}", json=payload, headers={"X-Service-Token": read_service_token("support-write")}, timeout=5)
    if not response.is_success:
        raise RuntimeError(f"Merchant repair failed with HTTP {response.status_code}")
    return response.json()


def execute_background_action(user: UserContext, action_id: str) -> dict[str, object]:
    from backend.app.support_action_plans import inventory_snapshot
    from backend.app.support_action_verification import wait_for_background_verification
    from backend.app.support_tools import execute_read_tool_batch

    action = get_action_plan(user, action_id)
    authorize_action_decision(user, action)
    if action["action_type"] not in {"refresh_inventory", "retry_failed_task"}:
        raise ValueError("Unsupported background action")
    if action["status"] in {"verified_resolved", "verification_failed", "blocked", "rejected", "expired"}:
        return get_action_details(user, action_id)
    if action["status"] == "awaiting_verification":
        return wait_for_background_verification(user, action_id)
    if action["status"] == "executing":
        receipt = get_existing_recovery_receipt(action)
        if receipt is not None:
            record_existing_submission(action, receipt)
            return wait_for_background_verification(user, action_id)
    if action["status"] not in {"approved", "executing"}:
        raise ValueError("Action is not approved")
    shop_id = str(action["shop_id"])
    object_id = str(action["external_order_id"])
    calls = [{"name": "GetShopSyncStatus", "args": {"shop_id": shop_id}}, {"name": "GetShopConnectionStatus", "args": {"shop_id": shop_id}}]
    if action["action_type"] == "refresh_inventory":
        calls.append({"name": "GetStockStatus", "args": {"shop_id": shop_id, "sku": object_id}})
    else:
        calls.extend([{"name": "GetWorkerTask", "args": {"shop_id": shop_id, "order_id": object_id}}, {"name": "GetOrder", "args": {"shop_id": shop_id, "order_id": object_id}}])
    evidence = execute_read_tool_batch(str(action["case_id"]), user, calls, shop_id, object_id, object_id)
    indexed = {record.tool_name: record for record in evidence}
    snapshot = action["source_snapshot"]
    connection = indexed["GetShopConnectionStatus"]
    shop = indexed["GetShopSyncStatus"]
    valid = shop.status == "success" and shop.response.get("version") == action["shop_version"] and connection.status == "success" and connection.response.get("connection_status") == "authorized"
    if action["action_type"] == "refresh_inventory":
        stock = indexed["GetStockStatus"]
        valid = valid and stock.status == "success" and inventory_snapshot(stock.response) == snapshot
    else:
        task = indexed["GetWorkerTask"]
        order = indexed["GetOrder"]
        valid = valid and task.status == "success" and task.response.get("retryable") is True and fields_match(task.response, snapshot["worker_task"], ("task_id", "version", "status", "attempts"))
        valid = valid and order.status == "success" and fields_match(order.response, snapshot, ("event_id", "version", "sku", "quantity", "amount_minor", "payment_status"))
    if not valid:
        mark_action_blocked(action_id, "SOURCE_OR_SHOP_CHANGED", evidence[0].evidence_id)
        return get_action_details(user, action_id)
    request_id = str(uuid5(NAMESPACE_URL, action["idempotency_key"]))
    lease = acquire_action_execution(action_id, request_id)
    if lease == "busy":
        return get_action_details(user, action_id)
    if lease == "submitted":
        return wait_for_background_verification(user, action_id)
    with get_connection() as database:
        approval = database.execute("SELECT decision_id::text, decided_by FROM support.action_decisions WHERE action_id = %s AND decision = 'approved'", (action_id,)).fetchone()
    payload = {"action_id": action_id, "request_id": request_id, "company_id": user.company_id, "shop_id": shop_id, "object_id": object_id, "source_snapshot": snapshot, "shop_version": action["shop_version"], "approval_id": approval["decision_id"], "approved_by": approval["decided_by"], "approval_expires_at": action["expires_at"].isoformat()}
    save_action_step(action_id, "scope_version_recheck", "passed", {"evidence_ids": [record.evidence_id for record in evidence]}, evidence[0].evidence_id)
    try:
        receipt = submit_background_repair(str(action_policy(action)["resource"]), payload)
    except (httpx.RequestError, RuntimeError) as error:
        status = "unknown" if isinstance(error, httpx.RequestError) else "failed"
        with get_connection() as database:
            database.execute("UPDATE support.action_executions SET status = %s, error_type = %s, updated_at = NOW() WHERE action_id = %s", (status, type(error).__name__, action_id))
            if status == "failed":
                database.execute("UPDATE support.action_proposals SET status = 'blocked', updated_at = NOW() WHERE action_id = %s", (action_id,))
        save_action_step(action_id, "execute", status, {"error_type": type(error).__name__})
        return get_action_details(user, action_id)
    record_existing_submission(action, receipt, reconciled=False)
    receipt_record = save_evidence(str(action["case_id"]), user.company_id, str(uuid4()), False, None, "RepairReceipt", {"action_id": action_id, "request_id": request_id}, receipt, "merchant", str(receipt.get("task_id") or action_id), "success", 0, current_trace_id())
    save_action_step(action_id, "execute", "submitted", {"request_id": request_id}, receipt_record.evidence_id)
    return wait_for_background_verification(user, action_id)

#已经有一个批准过的 Action Plan 后，这个文件负责最后检查一次，然后真正调用后台修复 API
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

# 发现当前情况已经不适合执行了，就把 Action 停掉
def mark_action_blocked(action_id: str, reason: str, evidence_id: str | None = None) -> None:
    with get_connection() as connection:
        connection.execute("UPDATE support.action_proposals SET status = 'blocked', updated_at = NOW() WHERE action_id = %s", (action_id,))

    evidence_ids: list[str] = []

    if evidence_id is not None and evidence_id != "":
        evidence_ids.append(evidence_id)

    save_action_step(action_id, "scope_version_recheck", "blocked", {"reason": reason, "evidence_ids": evidence_ids}, evidence_id)

#防止同一个 Action 被同时执行两次。
def acquire_action_execution(action_id: str, request_id: str) -> str:
    current_time = datetime.now(UTC)
    execution_lease_until = current_time + timedelta(seconds=ACTION_EXECUTION_LEASE_SECONDS)

    with get_connection() as connection:
        approval_row = connection.execute("""SELECT 1 FROM support.action_proposals p JOIN support.action_decisions d ON d.action_id = p.action_id
            WHERE p.action_id = %s AND p.status IN ('approved', 'executing') AND d.decision = 'approved' AND p.expires_at > NOW() FOR UPDATE OF p""", (action_id,)).fetchone()

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

#幂等性的问题具体实现
def get_existing_recovery_receipt(action_plan: dict[str, object]) -> dict[str, object] | None:
    merchant_url = os.getenv("MERCHANT_URL", "http://127.0.0.1:8002")

    resource_name = action_policy(action_plan)["resource"]

    receipt_result = call_read_service("GetRecoveryReceipt", "merchant", f"{merchant_url}/repairs/{resource_name}/{action_plan['action_id']}", str(action_plan["company_id"]), {})

    if receipt_result.status == "success":
        return receipt_result.response
    if receipt_result.status != "not_found":
        raise ValueError("Repair receipt could not be reconciled; do not resubmit yet")
    return None


def record_existing_submission(action_plan: dict[str, object], receipt: dict[str, object], reconciled: bool = True) -> None:
    with get_connection() as connection:
        connection.execute("UPDATE support.action_executions SET status = 'submitted', receipt = %s::jsonb, updated_at = NOW() WHERE action_id = %s", (json.dumps(receipt, default=str), action_plan["action_id"]))
        connection.execute("UPDATE support.action_proposals SET status = 'awaiting_verification', updated_at = NOW() WHERE action_id = %s", (action_plan["action_id"],))

    save_action_step(str(action_plan["action_id"]), "receipt_reconciliation", "accepted", {"recovered_after_unknown_result": reconciled, "task_id": receipt.get("task_id")})


@traceable(name="execute_order_recovery", run_type="chain")
def execute_order_recovery(user: UserContext, action_id: str) -> dict[str, object]:
    action_plan = get_action_plan(user, action_id)
    authorize_action_decision(user, action_plan)
    if action_plan["action_type"] not in {"recover_order", "retry_order_sync"}:
        raise ValueError("Action is not an order retry")

    if action_plan["status"] in {"verified_resolved", "verification_failed", "blocked", "rejected", "expired"}:
        return get_action_details(user, action_id)

    if action_plan["status"] == "awaiting_verification":
        from backend.app.support_action_verification import wait_for_order_verification
        return wait_for_order_verification(user, action_id)

    if action_plan["status"] == "executing":
        existing_receipt = get_existing_recovery_receipt(action_plan)

        if existing_receipt is not None:
            record_existing_submission(action_plan, existing_receipt)

            from backend.app.support_action_verification import wait_for_order_verification
            return wait_for_order_verification(user, action_id)

    if action_plan["status"] in {"approved", "executing"}:
        pass
    else:
        raise ValueError("Action is not approved")

    current_order = get_order(user, action_plan["shop_id"], action_plan["external_order_id"])
    current_shop_sync_status = get_shop_sync_status(user, action_plan["shop_id"])

    order_evidence_id = save_read_tool_evidence(str(action_plan["case_id"]), user, current_order)
    shop_evidence_id = save_read_tool_evidence(str(action_plan["case_id"]), user, current_shop_sync_status)

    source_snapshot = action_plan["source_snapshot"]
    compared_fields = ("event_id", "version", "sku", "quantity", "amount_minor", "payment_status")
    source_order_matches = current_order.status == "success" and fields_match(current_order.response, source_snapshot, compared_fields)

    if source_order_matches is False:
        mark_action_blocked(action_id, "SOURCE_VERSION_CHANGED", order_evidence_id)
        return get_action_details(user, action_id)

    if current_order.response.get("payment_status") != "paid":
        mark_action_blocked(action_id, "ORDER_NOT_PAID", order_evidence_id)
        return get_action_details(user, action_id)

    if current_shop_sync_status.status != "success" or current_shop_sync_status.response.get("version") != action_plan["shop_version"]:
        mark_action_blocked(action_id, "SHOP_VERSION_CHANGED", shop_evidence_id)
        return get_action_details(user, action_id)

    request_id = str(uuid5(NAMESPACE_URL, action_plan["idempotency_key"]))

    with get_connection() as connection:
        approval_decision = connection.execute("SELECT decision_id::text, decided_by FROM support.action_decisions WHERE action_id = %s AND decision = 'approved'", (action_id,)).fetchone()

        if approval_decision is None:
            raise ValueError("Approved decision record is missing")

    execution_status = acquire_action_execution(action_id, request_id)

    if execution_status == "busy":
        return get_action_details(user, action_id)

    if execution_status == "submitted":
        from backend.app.support_action_verification import wait_for_order_verification
        return wait_for_order_verification(user, action_id)

    save_action_step(action_id, "scope_version_recheck", "passed", {"source_version": action_plan["source_version"], "shop_version": action_plan["shop_version"], "evidence_ids": [order_evidence_id, shop_evidence_id]}, order_evidence_id)

    repair_payload = {
        "action_id": action_id,
        "request_id": request_id,
        "company_id": action_plan["company_id"],
        "shop_id": action_plan["shop_id"],
        "external_order_id": action_plan["external_order_id"],
        "source_event_id": str(action_plan["source_event_id"]),
        "source_version": action_plan["source_version"],
        "shop_version": action_plan["shop_version"],
        "source_snapshot": source_snapshot,
        "enable_order_sync": action_plan["enable_order_sync"],
        "approval_id": approval_decision["decision_id"],
        "approved_by": approval_decision["decided_by"],
        "approval_expires_at": action_plan["expires_at"].isoformat(),
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

    receipt_record = save_evidence(str(action_plan["case_id"]), user.company_id, str(uuid4()), False, None, "RecoverOrderReceipt", {"action_id": action_id, "request_id": request_id}, receipt, "merchant", receipt_id, "success", 0, current_trace_id())

    save_action_step(action_id, "execute", "submitted", {"request_id": request_id})
    save_action_step(action_id, "receipt_reconciliation", "accepted", {"duplicate": bool(receipt.get("duplicate")), "task_id": receipt.get("task_id"), "evidence_ids": [receipt_record.evidence_id]}, receipt_record.evidence_id)

    from backend.app.support_action_verification import wait_for_order_verification

    return wait_for_order_verification(user, action_id)


@traceable(name="execute_shipment_recovery", run_type="chain")
def execute_shipment_recovery(user: UserContext, action_id: str) -> dict[str, object]:
    action_plan = get_action_plan(user, action_id)
    authorize_action_decision(user, action_plan)

    if action_plan["action_type"] not in {"recover_shipment", "resend_shipment"}:
        raise ValueError("Action is not a shipment recovery")

    if action_plan["status"] in {"verified_resolved", "verification_failed", "blocked", "rejected", "expired"}:
        return get_action_details(user, action_id)

    if action_plan["status"] == "awaiting_verification":
        from backend.app.support_action_verification import wait_for_shipment_verification
        return wait_for_shipment_verification(user, action_id)

    if action_plan["status"] == "executing":
        existing_receipt = get_existing_recovery_receipt(action_plan)

        if existing_receipt is not None:
            record_existing_submission(action_plan, existing_receipt)

            from backend.app.support_action_verification import wait_for_shipment_verification
            return wait_for_shipment_verification(user, action_id)

    if action_plan["status"] in {"approved", "executing"}:
        pass
    else:
        raise ValueError("Action is not approved")

    shop_id = str(action_plan["shop_id"])
    order_id = str(action_plan["external_order_id"])

    warehouse_shipment = get_warehouse_shipment(user, shop_id, order_id)
    shipment_process = get_shipment_process_records(user, shop_id, order_id)
    platform_shipment = get_platform_shipment(user, shop_id, order_id)
    shop_sync_status = get_shop_sync_status(user, shop_id)
    source_order = get_order(user, shop_id, order_id)

    evidence_ids: list[str] = []

    for tool_result in (warehouse_shipment, shipment_process, platform_shipment, shop_sync_status, source_order):
        evidence_id = save_read_tool_evidence(str(action_plan["case_id"]), user, tool_result)
        evidence_ids.append(evidence_id)

    source_snapshot = action_plan["source_snapshot"]
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

    if shop_sync_status.status != "success" or shop_sync_status.response.get("version") != action_plan["shop_version"]:
        mark_action_blocked(action_id, "SHOP_VERSION_CHANGED", evidence_ids[3])
        return get_action_details(user, action_id)

    request_id = str(uuid5(NAMESPACE_URL, action_plan["idempotency_key"]))

    with get_connection() as connection:
        approval_decision = connection.execute("SELECT decision_id::text, decided_by FROM support.action_decisions WHERE action_id = %s AND decision = 'approved'", (action_id,)).fetchone()

        if approval_decision is None:
            raise ValueError("Approved decision record is missing")

    execution_status = acquire_action_execution(action_id, request_id)

    if execution_status == "busy":
        return get_action_details(user, action_id)

    if execution_status == "submitted":
        from backend.app.support_action_verification import wait_for_shipment_verification
        return wait_for_shipment_verification(user, action_id)

    save_action_step(action_id, "scope_version_recheck", "passed", {"source_version": action_plan["source_version"], "shop_version": action_plan["shop_version"], "evidence_ids": evidence_ids}, evidence_ids[0])

    platform_already_matches = platform_shipment.status == "success" and fields_match(platform_shipment.response, source_snapshot, shipment_fields)

    if platform_already_matches is True:
        receipt = {"accepted": True, "duplicate": True, "reconciled_from_platform": True}
        receipt_source_service = "platform"
    else:
        repair_payload = {
            "action_id": action_id,
            "request_id": request_id,
            "company_id": action_plan["company_id"],
            "shop_id": shop_id,
            "external_order_id": order_id,
            "shipment_id": str(action_plan["source_event_id"]),
            "shipment_version": action_plan["source_version"],
            "source_snapshot": source_snapshot,
            "enable_shipment_sync": action_plan["enable_shipment_sync"],
            "approval_id": approval_decision["decision_id"],
            "approved_by": approval_decision["decided_by"],
            "approval_expires_at": action_plan["expires_at"].isoformat(),
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

    receipt_record = save_evidence(str(action_plan["case_id"]), user.company_id, str(uuid4()), False, None, "RecoverShipmentReceipt", {"action_id": action_id, "request_id": request_id}, receipt, receipt_source_service, receipt_id, "success", 0, current_trace_id())

    save_action_step(action_id, "execute", "submitted", {"request_id": request_id, "reconciled": platform_already_matches})
    save_action_step(action_id, "receipt_reconciliation", "accepted", {"duplicate": bool(receipt.get("duplicate")), "task_id": receipt.get("task_id"), "evidence_ids": [receipt_record.evidence_id]}, receipt_record.evidence_id)

    from backend.app.support_action_verification import wait_for_shipment_verification

    return wait_for_shipment_verification(user, action_id)


# 严格按照“重新读取 → 对比 Snapshot → 防重复 → Write API → Receipt → Verification”走
# support_action_execution.py
# 【负责真正执行已经批准的 Action Plan】
#
#
# Approved Action Plan
# ↓
#
# 找到对应的 Execution Function
#
# ├── retry_order_sync
# │   → execute_order_recovery()
# │
# ├── resend_shipment
# │   → execute_shipment_recovery()
# │
# └── refresh_inventory / retry_failed_task
#     → execute_background_action()
#
# ↓
#
# 再次检查用户有没有执行权限
# ↓
#
# 检查 Action 当前状态
#
# 已经结束？
# → 直接返回结果
#
# 已经提交，正在等 Verification？
# → 直接进入 Verification
#
# ↓
#
# 重新查询最新后台状态
# 【执行之前最后查一次真实数据】
# ↓
#
# 和 Action Plan 里的 Snapshot / Version 比较
# ↓
#
# ├── 数据已经变化
# │   ↓
# │   mark_action_blocked()  如果发现当前后台状态已经和 Action Plan 不一样，就停止这个 Action。
# │   【停止执行，避免拿旧数据去修改现在的新状态】
# │
# └── 数据没有变化
#        ↓
#
#     acquire_action_execution()  幂等性
#     【防止同一个 Action 同时执行两次】
#        ↓
#
#     真正调用 Write API
#        ↓
#
#     后台返回 Receipt
#     【后台告诉我们：修复请求已经收到】
#        ↓
#
#     保存 Receipt 为 Evidence
#        ↓
#
#     status = awaiting_verification
#        ↓
#
#     进入 Verification
#     【重新查询后台，确认实际上有没有修好】


# Approved Action Plan
# ↓
# 1. 重新查最新后台
# ↓
# 2. 和 Snapshot 比较
# ↓
# 3. 防止重复执行
# ↓
# 4. 调 Write API 真正修改
# ↓
# 5. 保存 Receipt，然后进入 Verification
# Receipt 只代表后台收到修复请求，不代表已经修成功