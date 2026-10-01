import json
import time
from uuid import uuid4

from langsmith import traceable

from backend.app.database import get_connection
from backend.app.models import UserContext
from backend.app.support_action_store import fields_match, get_action_plan, get_evidence_ids, index_evidence_by_tool, save_action_step
from backend.app.support_tools import ReadToolResult, execute_read_tool_batch
from backend.app.trace import current_trace_id


def verify_background_action(user: UserContext, action_id: str) -> dict[str, object]:
    from backend.app.support_action_plans import inventory_snapshot
    from backend.app.support_action_store import get_action_details

    action = get_action_plan(user, action_id)
    if action["status"] not in {"awaiting_verification", "verified_resolved", "verification_failed"}:
        raise ValueError("Action must have a submitted repair before verification")
    shop_id = str(action["shop_id"])
    object_id = str(action["external_order_id"])
    if action["action_type"] == "refresh_inventory":
        calls = [{"name": "GetStockStatus", "args": {"shop_id": shop_id, "sku": object_id}}]
    else:
        calls = [{"name": "GetWorkerTask", "args": {"shop_id": shop_id, "order_id": object_id}}, {"name": "GetOrder", "args": {"shop_id": shop_id, "order_id": object_id}}, {"name": "GetOrderProcessRecords", "args": {"shop_id": shop_id, "order_id": object_id}}]
    evidence = execute_read_tool_batch(str(action["case_id"]), user, calls, shop_id, object_id, object_id)
    indexed = index_evidence_by_tool(evidence)
    if action["action_type"] == "refresh_inventory":
        stock = indexed["GetStockStatus"]
        task = (stock.response.get("merchant") or {}).get("task") or {}
        checks = {"facts_available": stock.status == "success", "source_matches": inventory_snapshot(stock.response) == action["source_snapshot"], "platform_matches": stock.response.get("assessment") == "consistent", "task_completed": task.get("task_status") == "completed"}
        failed = task.get("task_status") in {"failed", "blocked"}
    else:
        task = indexed["GetWorkerTask"]
        checks = check_order_recovery_facts(indexed["GetOrder"], indexed["GetOrderProcessRecords"], action["source_snapshot"])["checks"]
        checks["same_task_completed"] = task.status == "success" and task.response.get("task_id") == action["source_snapshot"]["worker_task"]["task_id"] and task.response.get("status") == "completed"
        failed = task.response.get("status") in {"failed", "blocked"}
    resolved = all_verification_checks_passed(checks)
    status = "verified_resolved" if resolved else "pending"
    if failed and not resolved:
        status = "verification_failed"
    save_action_verification(action_id, status, {"checks": checks}, get_evidence_ids(evidence))
    if status != "pending":
        with get_connection() as connection:
            connection.execute("UPDATE support.action_proposals SET status = %s, updated_at = NOW() WHERE action_id = %s", (status, action_id))
            connection.execute("UPDATE support.cases SET status = %s, outcome = %s, updated_at = NOW() WHERE case_id = %s", ("verified_resolved" if resolved else "pending_human", f"Verification: {status}", action["case_id"]))
        save_action_step(action_id, "verification", status, {"checks": checks}, evidence[0].evidence_id)
    return get_action_details(user, action_id)


def wait_for_background_verification(user: UserContext, action_id: str, timeout_seconds: float = 6) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        details = verify_background_action(user, action_id)
        if details["status"] in {"verified_resolved", "verification_failed"}:
            return details
        time.sleep(0.25)
    return verify_background_action(user, action_id)

#Execution 说“修复请求已经提交”，Verification 负责重新查询真实系统，确认问题是不是真的修好了。
#比较几个关键字段是不是完全一样。
#所有检查都通过，才算修复成功。
def all_verification_checks_passed(verification_checks: dict[str, bool]) -> bool:
    for check_passed in verification_checks.values():
        if check_passed is False:
            return False

    return True


#Order Recovery 到底有没有真的成功。
def check_order_recovery_facts(platform_order: ReadToolResult, order_process: ReadToolResult, expected_order: dict[str, object]) -> dict[str, object]:
    quantity_matches = order_process.response.get("quantity") == expected_order.get("quantity")
    source_quantity_matches = order_process.response.get("source_quantity") == expected_order.get("quantity")
    amount_matches = order_process.response.get("amount_minor") == expected_order.get("amount_minor")
    source_amount_matches = order_process.response.get("source_amount_minor") == expected_order.get("amount_minor")

    verification_checks = {
        "platform_available": platform_order.status == "success",
        "source_unchanged": fields_match(platform_order.response, expected_order, ("event_id", "version", "sku", "quantity", "amount_minor", "payment_status")),
        "merchant_available": order_process.status == "success",
        "event_matches": order_process.response.get("event_id") == expected_order.get("event_id"),
        "sku_matches": order_process.response.get("platform_sku") == expected_order.get("sku"),
        "mapping_matches": expected_order.get("merchant_sku") is None or order_process.response.get("merchant_sku") == expected_order["merchant_sku"],
        "quantity_matches": quantity_matches and source_quantity_matches,
        "amount_matches": amount_matches and source_amount_matches,
        "exactly_one_merchant_order": order_process.response.get("merchant_order_count") == 1,
        "task_completed": order_process.response.get("task_status") == "completed",
    }

    return {"resolved": all_verification_checks_passed(verification_checks), "checks": verification_checks}


def check_shipment_recovery_facts(warehouse_shipment: ReadToolResult, shipment_process: ReadToolResult, platform_shipment: ReadToolResult, expected_shipment: dict[str, object]) -> dict[str, object]:
    shipment_fields = ("shipment_id", "carrier", "tracking_number")

    verification_checks = {
        "warehouse_available": warehouse_shipment.status == "success",
        "merchant_available": shipment_process.status == "success",
        "platform_available": platform_shipment.status == "success",
        "one_actual_warehouse_shipment": warehouse_shipment.response.get("shipment_count") == 1,
        "warehouse_matches": fields_match(warehouse_shipment.response, expected_shipment, shipment_fields),
        "merchant_matches": fields_match(shipment_process.response, expected_shipment, shipment_fields),
        "platform_matches": fields_match(platform_shipment.response, expected_shipment, shipment_fields),
        "recovery_task_completed": shipment_process.response.get("task_status") == "completed",
        "warehouse_version_matches": warehouse_shipment.response.get("shipment_version") == expected_shipment.get("version", expected_shipment.get("shipment_version")),
        "merchant_version_matches": shipment_process.response.get("version") == expected_shipment.get("version", expected_shipment.get("shipment_version")),
        "platform_version_matches": platform_shipment.response.get("version") == expected_shipment.get("version", expected_shipment.get("shipment_version")),
    }

    return {"resolved": all_verification_checks_passed(verification_checks), "checks": verification_checks}


def save_action_verification(action_id: str, status: str, verification_details: dict[str, object], evidence_ids: list[str]) -> None:
    with get_connection() as connection:
        connection.execute(
            """INSERT INTO support.action_verifications (verification_id, action_id, status, details, evidence_ids, trace_id)
            VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s)
            ON CONFLICT (action_id) DO UPDATE SET status = EXCLUDED.status, details = EXCLUDED.details, evidence_ids = EXCLUDED.evidence_ids, trace_id = EXCLUDED.trace_id, checked_at = NOW()""",
            (str(uuid4()), action_id, status, json.dumps(verification_details), json.dumps(evidence_ids), current_trace_id()),
        )


@traceable(name="verify_order_recovery", run_type="chain")
def verify_order_recovery(user: UserContext, action_id: str, final_check: bool = False) -> dict[str, object]:
    action = get_action_plan(user, action_id)
    if action["status"] not in {"awaiting_verification", "verified_resolved", "verification_failed"}:
        raise ValueError("Action must have a submitted repair before verification")
    with get_connection() as connection:
        action_plan = connection.execute("SELECT action_id::text, case_id::text, company_id, shop_id, external_order_id, source_snapshot FROM support.action_proposals WHERE action_id = %s AND company_id = %s", (action_id, user.company_id)).fetchone()

    if action_plan is None:
        raise PermissionError("Action is not available in this company scope")

    read_tool_calls = [
        {"name": "GetOrder", "args": {"shop_id": action_plan["shop_id"], "order_id": action_plan["external_order_id"]}, "id": "verify-platform"},
        {"name": "GetOrderProcessRecords", "args": {"shop_id": action_plan["shop_id"], "order_id": action_plan["external_order_id"]}, "id": "verify-merchant"},
    ]

    evidence = execute_read_tool_batch(action_plan["case_id"], user, read_tool_calls, action_plan["shop_id"], action_plan["external_order_id"])
    evidence_by_tool_name = index_evidence_by_tool(evidence)

    platform_order = evidence_by_tool_name["GetOrder"]
    order_process = evidence_by_tool_name["GetOrderProcessRecords"]
    expected_order = action_plan["source_snapshot"]

    verification_result = check_order_recovery_facts(platform_order, order_process, expected_order)
    verification_checks = verification_result["checks"]
    is_resolved = verification_result["resolved"]

    if is_resolved is True:
        verification_status = "verified_resolved"
    elif final_check is True:
        verification_status = "verification_failed"
    else:
        verification_status = "pending"

    evidence_ids = get_evidence_ids(evidence)
    verification_details = {"checks": verification_checks, "order_id": action_plan["external_order_id"], "shop_id": action_plan["shop_id"]}

    save_action_verification(action_id, verification_status, verification_details, evidence_ids)

    if is_resolved is True or final_check is True:
        with get_connection() as connection:
            connection.execute("UPDATE support.action_proposals SET status = %s, updated_at = NOW() WHERE action_id = %s", (verification_status, action_id))
            connection.execute("UPDATE support.cases SET status = %s, outcome = %s, updated_at = NOW() WHERE case_id = %s", ("verified_resolved" if is_resolved else "pending_human", f"Verification: {verification_status}", action_plan["case_id"]))
            connection.execute("INSERT INTO support.action_steps (step_id, action_id, step_name, status, details, evidence_id, trace_id) VALUES (%s, %s, 'verification', %s, %s::jsonb, %s, %s)", (str(uuid4()), action_id, verification_status, json.dumps(verification_details), evidence_ids[0], current_trace_id()))

    from backend.app.support_action_store import get_action_details

    return get_action_details(user, action_id)


def wait_for_order_verification(user: UserContext, action_id: str, timeout_seconds: float = 6) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        action_details = verify_order_recovery(user, action_id)

        if action_details["status"] == "verified_resolved":
            return action_details

        time.sleep(0.25)

    return verify_order_recovery(user, action_id, final_check=True)


@traceable(name="verify_shipment_recovery", run_type="chain")
def verify_shipment_recovery(user: UserContext, action_id: str) -> dict[str, object]:
    action = get_action_plan(user, action_id)
    if action["status"] not in {"awaiting_verification", "verified_resolved", "verification_failed"}:
        raise ValueError("Action must have a submitted repair before verification")
    with get_connection() as connection:
        action_plan = connection.execute("SELECT action_id::text, case_id::text, company_id, shop_id, external_order_id, source_snapshot FROM support.action_proposals WHERE action_id = %s AND company_id = %s AND action_type IN ('recover_shipment', 'resend_shipment')", (action_id, user.company_id)).fetchone()

    if action_plan is None:
        raise PermissionError("Shipment action is not available in this company scope")

    read_tool_calls = [
        {"name": "GetWarehouseShipment", "args": {"shop_id": action_plan["shop_id"], "order_id": action_plan["external_order_id"]}, "id": "verify-warehouse-shipment"},
        {"name": "GetShipmentProcessRecords", "args": {"shop_id": action_plan["shop_id"], "order_id": action_plan["external_order_id"]}, "id": "verify-merchant-shipment"},
        {"name": "GetPlatformShipment", "args": {"shop_id": action_plan["shop_id"], "order_id": action_plan["external_order_id"]}, "id": "verify-platform-shipment"},
    ]

    evidence = execute_read_tool_batch(action_plan["case_id"], user, read_tool_calls, action_plan["shop_id"], action_plan["external_order_id"])
    evidence_by_tool_name = index_evidence_by_tool(evidence)

    warehouse_shipment = evidence_by_tool_name["GetWarehouseShipment"]
    shipment_process = evidence_by_tool_name["GetShipmentProcessRecords"]
    platform_shipment = evidence_by_tool_name["GetPlatformShipment"]
    expected_shipment = action_plan["source_snapshot"]

    verification_result = check_shipment_recovery_facts(warehouse_shipment, shipment_process, platform_shipment, expected_shipment)
    verification_checks = verification_result["checks"]
    is_resolved = verification_result["resolved"]

    if is_resolved is True:
        verification_status = "verified_resolved"
    else:
        verification_status = "pending"

    evidence_ids = get_evidence_ids(evidence)
    verification_details = {"checks": verification_checks, "order_id": action_plan["external_order_id"], "shop_id": action_plan["shop_id"]}

    save_action_verification(action_id, verification_status, verification_details, evidence_ids)

    if is_resolved is True:
        with get_connection() as connection:
            connection.execute("UPDATE support.action_proposals SET status = 'verified_resolved', updated_at = NOW() WHERE action_id = %s", (action_id,))
            connection.execute("UPDATE support.cases SET status = 'verified_resolved', outcome = 'Shipment facts verified', updated_at = NOW() WHERE case_id = %s", (action_plan["case_id"],))
            connection.execute("INSERT INTO support.action_steps (step_id, action_id, step_name, status, details, evidence_id, trace_id) VALUES (%s, %s, 'verification', 'verified_resolved', %s::jsonb, %s, %s)", (str(uuid4()), action_id, json.dumps(verification_details), evidence_ids[0], current_trace_id()))

    from backend.app.support_action_store import get_action_details

    return get_action_details(user, action_id)


def wait_for_shipment_verification(user: UserContext, action_id: str, timeout_seconds: float = 15) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        action_details = verify_shipment_recovery(user, action_id)

        if action_details["status"] == "verified_resolved":
            return action_details

        time.sleep(0.25)

    return verify_shipment_recovery(user, action_id)


# Support Agent
# ↓
# Read Tools
# ↓
# Evidence
# ↓
# Diagnosis
# ↓
# 目前：外部选择修复类型
# 以后：Agent 推荐 Candidate Action
# ↓
# Action Plan
# 重新检查是否可以这样修
# ↓
# Approval
# 允许执行？
# ↓
# Execution
# 真正调用 Write API
# ↓
# Receipt
# 后台说“请求收到”
# ↓
# Verification
# 重新调用 Read Tools
# ↓
# 检查真实后台状态
# ↓
# verified_resolved / verification_failed


# Read Tools
# ↓
# 发现问题

# Write Action
# ↓
# 修改问题

# Read Tools
# ↓
# 确认问题真的被修复
