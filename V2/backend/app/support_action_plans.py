import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from langsmith import traceable

from backend.app.database import get_connection
from backend.app.models import UserContext
from backend.app.support_action_registry import ACTION_ALIASES, action_policy
from backend.app.support_action_store import fields_match, get_action_details, get_evidence_ids, get_total_evidence_latency_ms, index_evidence_by_tool, save_action_step, save_read_tool_evidence
from backend.app.support_cases import update_case
from backend.app.support_diagnosis import CandidateAction
from backend.app.support_evidence import load_evidence
from backend.app.support_tools import ReadToolResult, call_read_service, execute_read_tool_batch

ACTION_APPROVAL_MINUTES = 10


def reusable_plan(connection, case_id: str, key: str) -> tuple[str | None, str]:
    connection.execute("SELECT case_id FROM support.cases WHERE case_id = %s FOR UPDATE", (case_id,)).fetchone()
    while True:
        existing = connection.execute("SELECT action_id::text, status, expires_at FROM support.action_proposals WHERE idempotency_key = %s", (key,)).fetchone()
        if existing is None:
            return None, key
        if existing["status"] == "proposed" and existing["expires_at"] <= datetime.now(UTC):
            connection.execute("UPDATE support.action_proposals SET status = 'expired', updated_at = NOW() WHERE action_id = %s", (existing["action_id"],))
        elif existing["status"] in {"proposed", "approved", "executing", "awaiting_verification"}:
            return existing["action_id"], key
        # A new attempt has its own key; old execution receipts remain immutable.
        key = hashlib.sha256(f"{key}:{existing['action_id']}".encode()).hexdigest()


def create_action_plan(user: UserContext, case_id: str, candidate: CandidateAction) -> dict[str, object]:
    context = get_recovery_case_context(case_id, user)
    candidate_type = ACTION_ALIASES.get(candidate.action_type, candidate.action_type)
    policy = action_policy({"action_type": candidate_type})
    records = {record.evidence_id: record for record in load_evidence(case_id)}
    if not candidate.evidence_ids or not set(candidate.evidence_ids).issubset(records):
        raise ValueError("Candidate Action must cite actual Evidence from this case")
    required_tools = {
        "retry_order_sync": {"GetOrder", "GetOrderProcessRecords"},
        "resend_shipment": {"GetWarehouseShipment", "GetShipmentProcessRecords", "GetPlatformShipment"},
        "refresh_inventory": {"GetStockStatus"},
        "retry_failed_task": {"GetWorkerTask"},
        "request_reauthorization": {"GetShopConnectionStatus"},
    }
    cited_tools = set()
    for evidence_id in candidate.evidence_ids:
        record = records[evidence_id]
        if record.request.get("shop_id") != context["known_shop_id"]:
            raise ValueError("Candidate Evidence is outside the current shop scope")
        if "order_id" in record.request and record.request["order_id"] != context["known_order_id"]:
            raise ValueError("Candidate Evidence is outside the current order scope")
        if "sku" in record.request and record.request["sku"] != context["known_sku"]:
            raise ValueError("Candidate Evidence is outside the current SKU scope")
        if record.status in {"success", "empty", "not_found"}:
            cited_tools.add(record.tool_name)
    if not required_tools[candidate_type].issubset(cited_tools):
        raise ValueError("Candidate Action lacks relevant supporting Evidence")
    builder = globals()[policy["builder"]]
    if candidate_type in {"retry_order_sync", "retry_failed_task", "resend_shipment"}:
        details = builder(user, case_id, action_type=candidate_type)
    else:
        details = builder(user, case_id)
    if details.get("action_id"):
        save_action_step(str(details["action_id"]), "candidate_validation", "passed", candidate.model_dump())
    return details


def inventory_snapshot(facts: dict[str, object]) -> dict[str, object]:
    merchant = facts.get("merchant") or {}
    rule = merchant.get("rule") or {}
    warehouse = facts.get("warehouse") or {}
    return {"warehouse_sku": rule.get("warehouse_sku"), "rule_version": rule.get("rule_version"), "safety_stock": rule.get("safety_stock"), "warehouse_version": warehouse.get("version"), "physical_quantity": warehouse.get("physical_quantity"), "reserved_quantity": warehouse.get("reserved_quantity"), "expected_quantity": facts.get("expected_quantity")}


def build_inventory_action_plan(user: UserContext, case_id: str) -> dict[str, object]:
    context = get_recovery_case_context(case_id, user)
    shop_id = str(context["known_shop_id"])
    sku = context["known_sku"]
    if not sku:
        raise ValueError("Inventory refresh requires a known SKU")
    calls = [{"name": "GetStockStatus", "args": {"shop_id": shop_id, "sku": sku}}, {"name": "GetShopSyncStatus", "args": {"shop_id": shop_id}}, {"name": "GetShopConnectionStatus", "args": {"shop_id": shop_id}}]
    evidence = execute_read_tool_batch(case_id, user, calls, shop_id, "", str(sku))
    indexed = index_evidence_by_tool(evidence)
    stock = indexed["GetStockStatus"]
    connection = indexed["GetShopConnectionStatus"]
    shop = indexed["GetShopSyncStatus"]
    if stock.status != "success" or stock.response.get("assessment") not in {"difference", "consistent"}:
        raise ValueError("Inventory facts are incomplete or within the normal propagation window")
    if stock.response["assessment"] == "consistent":
        return {"status": "no_action_needed", "case_id": case_id, "evidence_ids": get_evidence_ids(evidence)}
    if shop.status != "success" or connection.status != "success" or connection.response.get("connection_status") != "authorized":
        raise ValueError("Shop connection requires user action or retry later")
    task = (stock.response.get("merchant") or {}).get("task") or {}
    if task.get("task_status") in {"pending", "processing"}:
        raise ValueError("Inventory publish is already running; wait before retrying")
    snapshot = inventory_snapshot(stock.response)
    if any(value is None for value in snapshot.values()):
        raise ValueError("Inventory snapshot is incomplete")
    platform = stock.response.get("platform") or {}
    if platform.get("source_version", 0) > snapshot["warehouse_version"]:
        raise ValueError("Platform inventory is newer than the source; human review required")
    key = hashlib.sha256(f"{case_id}:refresh_inventory:{shop_id}:{sku}:{shop.response['version']}:{json.dumps(snapshot, sort_keys=True)}".encode()).hexdigest()
    action_id = str(uuid4())
    with get_connection() as database:
        existing_id, key = reusable_plan(database, case_id, key)
        if existing_id:
            return get_action_details(user, existing_id)
        database.execute("""INSERT INTO support.action_proposals (action_id, case_id, company_id, shop_id, external_order_id, action_type, status, source_version, shop_version, source_snapshot, evidence_ids, idempotency_key, proposed_by, expires_at)
            VALUES (%s, %s, %s, %s, %s, 'refresh_inventory', 'proposed', %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s)""", (action_id, case_id, user.company_id, shop_id, sku, snapshot["warehouse_version"], shop.response["version"], json.dumps(snapshot), json.dumps(get_evidence_ids(evidence)), key, user.user_id, datetime.now(UTC) + timedelta(minutes=ACTION_APPROVAL_MINUTES)))
    save_action_step(action_id, "action_plan", "passed", {"action_type": "refresh_inventory", "sku": sku}, stock.evidence_id)
    return get_action_details(user, action_id)


def build_reauthorization_action(user: UserContext, case_id: str) -> dict[str, object]:
    context = get_recovery_case_context(case_id, user)
    shop_id = str(context["known_shop_id"])
    evidence = execute_read_tool_batch(case_id, user, [{"name": "GetShopConnectionStatus", "args": {"shop_id": shop_id}}], shop_id, "")
    connection = evidence[0]
    if connection.status != "success" or connection.response.get("connection_status") not in {"auth_expired", "forbidden"}:
        raise ValueError("Current shop connection does not require reauthorization")
    return {"status": "user_action_required", "case_id": case_id, "action_type": "request_reauthorization", "shop_id": shop_id, "instructions": "请店铺授权人在平台完成重新授权，然后返回重新检查。", "evidence_ids": get_evidence_ids(evidence), **{field: action_policy({"action_type": "request_reauthorization"})[field] for field in ("risk_level", "approval_requirement")}}

#根据最新后台状态，判断现在是否真的可以创建一个修复方案
#先找到这个 Case 要修哪个 Shop、哪个 Order
def get_recovery_case_context(case_id: str, user: UserContext) -> dict[str, object]:
    with get_connection() as connection:
        case_row = connection.execute("""SELECT c.case_id::text, c.conversation_id::text, c.company_id, h.known_shop_id, h.known_order_id, h.known_sku
            FROM support.cases c JOIN support.handoffs h ON h.handoff_id = c.handoff_id
            JOIN support.conversations v ON v.conversation_id = c.conversation_id
            WHERE c.case_id = %s AND c.company_id = %s AND v.user_id = %s""", (case_id, user.company_id, user.user_id)).fetchone()

    if case_row is None:
        raise PermissionError("Support case is not available in this user scope")

    if case_row["known_shop_id"] is None:
        raise ValueError("Case requires known shop_id")

    return dict(case_row)

#订单修复额外需要的一次查询
def get_sku_mapping(user: UserContext, shop_id: str, platform_sku: str) -> ReadToolResult:
    merchant_url = os.getenv("MERCHANT_URL", "http://127.0.0.1:8002")
    sku_mapping_result = call_read_service("GetSkuMapping", "merchant", f"{merchant_url}/internal/shops/{shop_id}/mappings/{platform_sku}", user.company_id, {})
    sku_mapping_result.request = {"shop_id": shop_id, "platform_sku": platform_sku}

    return sku_mapping_result


def build_order_recovery_idempotency_key(company_id: str, shop_id: str, external_order_id: str, event_id: str, source_version: int, enable_order_sync: bool) -> str:
    key_text = f"recover_order:{company_id}:{shop_id}:{external_order_id}:{event_id}:{source_version}:{enable_order_sync}"

    return hashlib.sha256(key_text.encode("utf-8")).hexdigest()


def build_shipment_recovery_idempotency_key(company_id: str, shop_id: str, external_order_id: str, shipment_id: str, source_version: int, enable_shipment_sync: bool) -> str:
    key_text = f"recover_shipment:{company_id}:{shop_id}:{external_order_id}:{shipment_id}:{source_version}:{enable_shipment_sync}"

    return hashlib.sha256(key_text.encode("utf-8")).hexdigest()

#判断当前订单是否适合执行“重新同步订单”
@traceable(name="build_order_action_plan", run_type="chain")
def build_order_action_plan(user: UserContext, case_id: str, enable_order_sync: bool = False, action_type: str = "retry_order_sync") -> dict[str, object]:
    case_context = get_recovery_case_context(case_id, user)
    shop_id = str(case_context["known_shop_id"])
    if not case_context["known_order_id"]:
        raise ValueError("Case requires known order_id")
    order_id = str(case_context["known_order_id"])

    read_tool_calls = [
        {"name": "GetOrder", "args": {"shop_id": shop_id, "order_id": order_id}, "id": "plan-order"},
        {"name": "GetOrderProcessRecords", "args": {"shop_id": shop_id, "order_id": order_id}, "id": "plan-process"},
        {"name": "GetShopSyncStatus", "args": {"shop_id": shop_id}, "id": "plan-shop"},
        {"name": "GetShopConnectionStatus", "args": {"shop_id": shop_id}, "id": "plan-connection"},
    ]

    if action_type == "retry_failed_task":
        read_tool_calls.append({"name": "GetWorkerTask", "args": {"shop_id": shop_id, "order_id": order_id}, "id": "plan-worker"})
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
        from backend.app.support_action_verification import check_order_recovery_facts
        if not check_order_recovery_facts(source_order, order_process, source_order_data)["resolved"]:
            raise ValueError("Existing merchant order conflicts with the source; human review required")
        update_case(case_id, "diagnosed", "订单已满足目标状态，无需创建恢复方案。", len(evidence), get_total_evidence_latency_ms(evidence))
        return {"status": "no_action_needed", "case_id": case_id, "evidence_ids": get_evidence_ids(evidence)}

    if shop_sync_status.status != "success" or shop_connection_status.status != "success":
        raise ValueError("Shop state could not be verified")

    if shop_sync_status.response.get("sync_enabled") is not True and enable_order_sync is False:
        raise ValueError("Order sync is disabled; enabling it requires an explicit plan option")

    if shop_connection_status.response.get("connection_status") != "authorized":
        raise ValueError("Shop connection requires user action or retry later")
    if order_process.status not in {"success", "empty", "not_found"}:
        raise ValueError("Order processing state could not be verified")
    if order_process.response.get("task_status") in {"pending", "processing"} and action_type != "retry_failed_task":
        raise ValueError("Order processing is still active; wait before retrying")

    sku_mapping = get_sku_mapping(user, shop_id, str(source_order_data["sku"]))
    sku_mapping_evidence_id = save_read_tool_evidence(case_id, user, sku_mapping)

    if sku_mapping.status != "success" or sku_mapping.response.get("active") is not True:
        raise ValueError("SKU mapping must exist before order recovery")

    snapshot_fields = ("event_id", "version", "sku", "quantity", "amount_minor", "payment_status")
    source_snapshot: dict[str, object] = {}

    for field_name in snapshot_fields:
        source_snapshot[field_name] = source_order_data[field_name]
    source_snapshot["merchant_sku"] = sku_mapping.response["merchant_sku"]
    if action_type == "retry_failed_task":
        task_record = evidence_by_tool_name["GetWorkerTask"]
        if task_record.status != "success" or not task_record.response.get("retryable"):
            raise ValueError("Task is not safely retryable")
        source_snapshot["worker_task"] = {field: task_record.response[field] for field in ("task_id", "version", "status", "attempts")}

    evidence_ids = get_evidence_ids(evidence)
    evidence_ids.append(sku_mapping_evidence_id)

    idempotency_key = build_order_recovery_idempotency_key(user.company_id, shop_id, order_id, str(source_order_data["event_id"]), int(source_order_data["version"]), enable_order_sync)
    idempotency_key = hashlib.sha256(f"{case_id}:{action_type}:{idempotency_key}:{json.dumps(source_snapshot, sort_keys=True)}:{shop_sync_status.response['version']}".encode()).hexdigest()
    action_id = str(uuid4())
    approval_expires_at = datetime.now(UTC) + timedelta(minutes=ACTION_APPROVAL_MINUTES)

    with get_connection() as connection:
        existing_id, idempotency_key = reusable_plan(connection, case_id, idempotency_key)
        if existing_id:
            return get_action_details(user, existing_id)

        connection.execute("""INSERT INTO support.action_proposals (action_id, case_id, company_id, shop_id, external_order_id, action_type, status, source_event_id, source_version, shop_version, source_snapshot, enable_order_sync, evidence_ids, idempotency_key, proposed_by, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, 'proposed', %s, %s, %s, %s::jsonb, %s, %s::jsonb, %s, %s, %s)""", (action_id, case_id, user.company_id, shop_id, order_id, action_type, source_order_data["event_id"], source_order_data["version"], shop_sync_status.response["version"], json.dumps(source_snapshot), enable_order_sync, json.dumps(evidence_ids), idempotency_key, user.user_id, approval_expires_at))

    save_action_step(action_id, "action_plan", "passed", {"action_type": action_type, "shop_id": shop_id, "order_id": order_id, "enable_order_sync": enable_order_sync, "evidence_ids": evidence_ids}, source_order.evidence_id)
    save_action_step(action_id, "policy_check", "passed", {"payment_status": "paid", "mapping_active": True, "connection_authorized": True, "source_version": source_order_data["version"], "shop_version": shop_sync_status.response["version"], "evidence_ids": evidence_ids}, sku_mapping_evidence_id)

    return get_action_details(user, action_id)


@traceable(name="build_shipment_action_plan", run_type="chain")
def build_shipment_action_plan(user: UserContext, case_id: str, enable_shipment_sync: bool = False, action_type: str = "resend_shipment") -> dict[str, object]:
    case_context = get_recovery_case_context(case_id, user)
    shop_id = str(case_context["known_shop_id"])
    order_id = str(case_context["known_order_id"])

    read_tool_calls = [
        {"name": "GetOrder", "args": {"shop_id": shop_id, "order_id": order_id}, "id": "plan-source-order"},
        {"name": "GetWarehouseShipment", "args": {"shop_id": shop_id, "order_id": order_id}, "id": "plan-warehouse-shipment"},
        {"name": "GetShipmentProcessRecords", "args": {"shop_id": shop_id, "order_id": order_id}, "id": "plan-merchant-shipment"},
        {"name": "GetPlatformShipment", "args": {"shop_id": shop_id, "order_id": order_id}, "id": "plan-platform-shipment"},
        {"name": "GetShopSyncStatus", "args": {"shop_id": shop_id}, "id": "plan-shop"},
        {"name": "GetShopConnectionStatus", "args": {"shop_id": shop_id}, "id": "plan-connection"},
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
    if not expected_shipment["carrier"] or not expected_shipment["tracking_number"] or not fields_match(shipment_process.response, expected_shipment, shipment_fields) or shipment_process.response.get("version") != warehouse_shipment.response.get("shipment_version"):
        raise ValueError("Shipment facts conflict or tracking information is incomplete")

    if platform_shipment.status == "success" and fields_match(platform_shipment.response, expected_shipment, shipment_fields):
        update_case(case_id, "diagnosed", "平台发货状态已满足目标，无需创建恢复方案。", len(evidence), get_total_evidence_latency_ms(evidence))
        return {"status": "no_action_needed", "case_id": case_id, "evidence_ids": get_evidence_ids(evidence)}

    if platform_shipment.status in {"not_found", "empty"}:
        pass
    else:
        raise ValueError("Platform shipment state conflicts with the warehouse fact")

    connection_status = evidence_by_tool_name["GetShopConnectionStatus"]
    if connection_status.status != "success" or connection_status.response.get("connection_status") != "authorized":
        raise ValueError("Shop connection requires user action or retry later")
    if shop_sync_status.status != "success":
        raise ValueError("Shop state could not be verified")

    if shop_sync_status.response.get("shipment_sync_enabled") is not True and enable_shipment_sync is False:
        raise ValueError("Shipment sync is disabled; enabling it requires an explicit plan option")

    source_snapshot = dict(expected_shipment)
    source_snapshot["version"] = warehouse_shipment.response["shipment_version"]
    source_snapshot["shipped_at"] = warehouse_shipment.response["shipped_at"]
    source_snapshot["order_event_id"] = source_order.response["event_id"]
    source_snapshot["order_version"] = source_order.response["version"]
    source_snapshot["payment_status"] = source_order.response["payment_status"]

    evidence_ids = get_evidence_ids(evidence)
    idempotency_key = build_shipment_recovery_idempotency_key(user.company_id, shop_id, order_id, str(source_snapshot["shipment_id"]), int(source_snapshot["version"]), enable_shipment_sync)
    idempotency_key = hashlib.sha256(f"{case_id}:{idempotency_key}:{shop_sync_status.response['version']}".encode()).hexdigest()
    action_id = str(uuid4())
    approval_expires_at = datetime.now(UTC) + timedelta(minutes=ACTION_APPROVAL_MINUTES)

    with get_connection() as connection:
        existing_id, idempotency_key = reusable_plan(connection, case_id, idempotency_key)
        if existing_id:
            return get_action_details(user, existing_id)

        connection.execute("""INSERT INTO support.action_proposals
            (action_id, case_id, company_id, shop_id, external_order_id, action_type, status, source_event_id, source_version, shop_version, source_snapshot, enable_shipment_sync, evidence_ids, idempotency_key, proposed_by, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, 'proposed', %s, %s, %s, %s::jsonb, %s, %s::jsonb, %s, %s, %s)""", (action_id, case_id, user.company_id, shop_id, order_id, action_type, source_snapshot["shipment_id"], source_snapshot["version"], shop_sync_status.response["version"], json.dumps(source_snapshot, default=str), enable_shipment_sync, json.dumps(evidence_ids), idempotency_key, user.user_id, approval_expires_at))

    recovery_steps: list[str] = []

    if enable_shipment_sync is True:
        recovery_steps.append("enable_shipment_sync")

    recovery_steps.append("resend_specific_shipment")

    save_action_step(action_id, "action_plan", "passed", {"action_type": action_type, "shop_id": shop_id, "order_id": order_id, "steps": recovery_steps, "evidence_ids": evidence_ids}, warehouse_shipment.evidence_id)
    save_action_step(action_id, "policy_check", "passed", {"warehouse_shipment_count": 1, "merchant_matches": True, "source_version": source_snapshot["version"], "shop_version": shop_sync_status.response["version"], "evidence_ids": evidence_ids}, shipment_process.evidence_id)

    return get_action_details(user, action_id)




# build_order_action_plan()
# → 订单恢复方案   解决： 订单应该进入 Merchant，但是没进去

# build_shipment_action_plan()
# → 发货恢复方案    解决： 仓库已经发货，但发货信息没有正确到达 Platform



# Support Case
# ↓
# 取得 shop_id / order_id
# ↓
# 重新查询最新后台状态
# ↓
# 得到新的 Evidence
# ↓
# 检查当前情况是否允许修
# ↓
# 如果已经好了
# → no_action_needed

# 如果不能安全修
# → 拒绝创建 Action Plan

# 如果可以修
# ↓
# 记录当前数据快照
# ↓
# 生成 action_id
# ↓
# 生成防重复 key
# ↓
# 设置 10 分钟审批有效期
# ↓
# 保存 Action Plan
# ↓
# 等待 Approval


# Diagnosis Evidence
# ↓
# 用户决定要修

# Action Plan
# ↓
# 重新读取最新后台状态
# ↓
# 新的 Evidence
# ↓
# 普通代码检查能不能修
# ↓
# 保存当前 Snapshot / Version
# ↓
# Action Plan
# ↓
# 等待 Approval

# Support Diagnosis
# = 哪里坏了？

# Action Plan
# = 现在还能不能这样修？

# Approval
# = 谁允许修？

# Execution
# = 真正修

# Verification
# = 修成功了吗？
