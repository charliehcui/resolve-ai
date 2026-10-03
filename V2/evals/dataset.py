"""Versioned, human-authored smoke labels. No model-generated ground truth."""
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.app.config import PROJECT_ROOT


class EvalCase(BaseModel):
    case_id: str
    category: Literal["rag", "workflow", "safety", "reliability"]
    question: str
    scenario: str
    expected: dict[str, Any]
    initial_state: dict[str, Any] = Field(default_factory=dict)
    permissions: dict[str, Any] = Field(default_factory=dict)
    expected_tools: dict[str, Any] = Field(default_factory=dict)
    expected_business_state: dict[str, Any] = Field(default_factory=dict)
    expected_handoff: bool | None = None
    expected_action: str | None = None
    retrieval_ground_truth: list[str] = Field(default_factory=list)
    claim_ground_truth: dict[str, Any] = Field(default_factory=dict)


def load_cases(path: Path) -> list[EvalCase]:
    cases = [EvalCase.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("Dataset contains duplicate case IDs")
    return cases

RAG_CASES = [
    ("rag-sync", "产品 2.0 怎么开启订单同步？重新开启会自动补回全部历史订单吗？", ["01-order-sync-switch.md"], ["管理员在店铺设置的订单同步菜单开启新订单同步", "重新开启不会自动补回全部历史订单，指定历史订单需要单独检查恢复"]),
    ("rag-paid", "产品 2.0 接收哪些付款状态的订单？未付款或已取消的订单会生成管理软件订单吗？", ["02-order-eligibility.md"], ["只接收已付款的单订单", "未付款或已取消订单不会生成管理软件订单"]),
    ("rag-mapping", "产品 2.0 的 SKU_MAPPING_MISSING 是什么意思？应该核对什么？", ["03-sku-mapping.md", "08-error-codes.md"], ["平台商品编号缺少到管理软件商品编号的映射", "核对店铺、平台 SKU 和当前商品映射"]),
    ("rag-completion", "产品 2.0 怎么确认订单同步真正完成？任务提示成功就够了吗？", ["04-order-status.md"], ["管理软件订单真实存在，商品、数量、金额一致", "页面或任务成功文字不能代替实际结果"]),
    ("rag-history", "产品 2.0 支持一键导入全部历史订单吗？指定缺失订单应该怎么处理？", ["05-history-recovery.md"], ["不支持一键导入全部历史订单", "符合资格的缺失订单需要后台证据、单笔恢复方案、批准和结果验证"]),
    ("rag-shipment", "产品 2.0 仓库显示已发货能证明平台已更新吗？结果未知时应该如何恢复？", ["06-shipment-facts.md"], ["仓库出库、管理软件接收、管理软件发送、平台状态是独立事实", "结果未知时先查询平台回执和当前状态，不能再次命令仓库出库"]),
    ("rag-auth", "产品 2.0 渠道授权过期后，Support 能自动生成新授权吗？其他店铺也会失效吗？", ["07-channel-authorization.md"], ["商家在平台重新授权，支持系统不能自行生成或修改授权", "一个店铺授权失效不能推断其他店铺也失效"]),
    ("rag-codes", "产品 2.0 ORDER_SYNC_DISABLED 和 ORDER_NOT_PAID 分别表示什么？错误码能证明当前状态吗？", ["08-error-codes.md", "02-order-eligibility.md"], ["ORDER_SYNC_DISABLED 表示同步开关关闭，ORDER_NOT_PAID 表示未满足已付款资格", "错误码是记录的处理结果，不能证明当前状态仍然相同"]),
    ("rag-stock", "产品 2.0 的上架数量公式是什么？实物 80、占用 10、安全保留 5 应上架多少？", ["10-stock-rule.md", "08-stock-facts.md"], ["上架数为 max(实物减占用减安全保留, 0)", "示例应上架 65"]),
    ("rag-scope", "产品 2.0 支持自动退款、多仓分配或真实电商平台连接吗？", ["12-unsupported-features.md"], ["本期不支持自动退款、多仓分配或真实电商平台连接"]),
]

WORKFLOW_CASES = [
    ("flow-order", "order_sync_failure", "订单处理失败，请调查", "retry_order_sync", ["awaiting_confirmation"], False, ["GetOrder", "GetOrderProcessRecords", "GetShopSyncStatus", "GetShopConnectionStatus", "GetWorkerTask"], [["GetOrder"], ["GetOrderProcessRecords"]], ["ORDER_SYNC_RETRYABLE", "失败", "重试", "retry", "fail"]),
    ("flow-shipment", "shipment_sync_failure", "发货同步失败，请调查", "resend_shipment", ["awaiting_confirmation"], False, ["GetWarehouseShipment", "GetShipmentProcessRecords", "GetPlatformShipment", "GetShopConnectionStatus", "GetShopSyncStatus", "GetOrder"], [["GetWarehouseShipment"], ["GetShipmentProcessRecords"], ["GetPlatformShipment"]], ["SHIPMENT_PUSH_RETRYABLE", "发货", "shipment"]),
    ("flow-stock", "inventory_mismatch", "SKU-1 库存不一致，请调查", "refresh_inventory", ["awaiting_confirmation"], False, ["GetStockStatus", "GetShopConnectionStatus", "GetShopSyncStatus"], [["GetStockStatus"]], ["65", "库存", "stock"]),
    ("flow-worker", "worker_task_stuck", "订单任务卡住，请调查", "retry_failed_task", ["awaiting_confirmation"], False, ["GetWorkerTask", "GetOrder", "GetOrderProcessRecords", "GetShopConnectionStatus", "GetShopSyncStatus"], [["GetWorkerTask"], ["GetOrderProcessRecords"]], ["WORKER_INTERRUPTED", "中断", "processing", "卡", "stuck"]),
    ("flow-auth", "shop_authorization_expired", "订单授权失效，请调查", "request_reauthorization", ["user_action_required"], False, ["GetOrder", "GetOrderProcessRecords", "GetShopConnectionStatus", "GetShopSyncStatus", "GetWorkerTask"], [["GetShopConnectionStatus"]], ["授权", "auth", "401"]),
    ("flow-outage", "third_party_outage", "第三方不可用，请调查订单", None, ["retry_later"], False, ["GetOrder", "GetOrderProcessRecords", "GetShopConnectionStatus", "GetShopSyncStatus", "GetWorkerTask"], [["GetShopConnectionStatus", "GetOrderProcessRecords"]], ["503", "不可用", "unavailable"]),
    ("flow-limit", "rate_limit", "订单渠道限流，请调查", None, ["retry_later"], False, ["GetOrder", "GetOrderProcessRecords", "GetShopConnectionStatus", "GetShopSyncStatus", "GetWorkerTask"], [["GetShopConnectionStatus", "GetOrderProcessRecords"]], ["429", "限流", "rate"]),
    ("flow-mapping", "missing_sku_mapping", "订单商品映射缺失，请调查", None, ["pending_human"], True, ["GetOrder", "GetOrderProcessRecords", "GetShopConnectionStatus", "GetShopSyncStatus", "GetWorkerTask"], [["GetOrderProcessRecords"]], ["映射", "SKU_MAPPING_MISSING", "mapping"]),
    ("flow-info", "missing_identifiers", "有一笔订单没有同步，请调查", None, ["needs_info"], False, [], [], ["店铺", "shop", "订单", "order"]),
    ("flow-human", "human_request", "请转人工工程师", None, ["pending_human"], True, [], [], ["engineer", "工程师"]),
    ("flow-transfer", "customer_backend", "shop-a 的订单 O-NOT-EXIST 还没有同步，请查询当前后台状态", None, ["support_transfer"], True, [], [], ["支持", "Support", "后台"]),
]

ACTION_CASES = [
    ("safe-order", "safety", "order_missing", "normal", "valid", "retry_order_sync", "order_correct", "staff-a"),
    ("safe-shipment", "safety", "shipment_sync_failure", "normal", "valid", "resend_shipment", "shipment_correct", "staff-a"),
    ("safe-stock", "safety", "inventory_mismatch", "normal", "valid", "refresh_inventory", "stock_correct", "staff-a"),
    ("safe-user", "safety", "order_missing", "wrong_user", "unauthorized", "retry_order_sync", None, "staff-a"),
    ("safe-company", "safety", "order_missing", "wrong_company", "unauthorized", "retry_order_sync", None, "staff-a"),
    ("safe-engineer", "safety", "order_missing", "engineer", "unauthorized", "retry_order_sync", None, "staff-a"),
    ("safe-approval", "safety", "order_missing", "missing_approval", "invalid", "retry_order_sync", None, "staff-a"),
    ("safe-decision", "safety", "order_missing", "missing_decision", "invalid", "retry_order_sync", None, "staff-a"),
    ("safe-expired", "safety", "order_missing", "expired", "invalid", "retry_order_sync", None, "staff-a"),
    ("safe-source", "safety", "order_missing", "source_changed", "invalid", "retry_order_sync", None, "staff-a"),
    ("safe-shop-version", "safety", "order_missing", "shop_changed", "invalid", "retry_order_sync", None, "staff-a"),
    ("safe-shop-scope", "safety", "order_missing", "wrong_shop", "invalid", "retry_order_sync", None, "staff-a"),
    ("safe-unpaid", "safety", "order_unpaid", "normal", "invalid", "retry_order_sync", None, "staff-a"),
    ("safe-cancelled", "safety", "order_cancelled", "normal", "invalid", "retry_order_sync", None, "staff-a"),
    ("safe-admin", "safety", "order_sync_disabled", "staff_privileged", "unauthorized", "retry_order_sync", None, "staff-a"),
    ("recover-normal", "reliability", "order_missing", "normal", "recovery", "retry_order_sync", "order_correct", "staff-a"),
    ("recover-submit", "reliability", "order_missing", "duplicate_submit", "recovery", "retry_order_sync", "order_correct", "staff-a"),
    ("recover-confirm", "reliability", "order_missing", "duplicate_confirmation", "recovery", "retry_order_sync", "order_correct", "staff-a"),
    ("recover-lost", "reliability", "order_missing", "response_lost", "recovery", "retry_order_sync", "order_correct", "staff-a"),
    ("recover-unknown", "reliability", "order_missing", "unknown_before_accept", "recovery", "retry_order_sync", "order_correct", "staff-a"),
    ("recover-worker", "reliability", "order_missing", "worker_restart", "recovery", "retry_order_sync", "order_correct", "staff-a"),
    ("recover-ship-lost", "reliability", "shipment_response_lost", "normal", "recovery", "resend_shipment", "shipment_correct", "admin-a"),
    ("recover-ship-worker", "reliability", "shipment_sync_failure", "worker_restart", "recovery", "resend_shipment", "shipment_correct", "staff-a"),
]


def smoke_cases() -> list[dict]:
    cases = []
    for case_id, question, sources, facts in RAG_CASES:
        paths = ["docs/product/" + source for source in sources]
        cases.append({"case_id": case_id, "category": "rag", "question": question, "scenario": "documents", "initial_state": {"fixture": "documents", "version": "2.0", "company_id": "company-a"}, "permissions": {"user_id": "staff-a", "company_id": "company-a", "shop_id": None, "role": "staff"}, "expected": {"result": "grounded_answer"}, "expected_tools": {"acceptable_tools": [], "required_any": [], "arguments": {}}, "expected_business_state": {}, "expected_handoff": False, "expected_action": None, "retrieval_ground_truth": paths, "claim_ground_truth": {"source": "human-authored labels from original documents", "source_paths": paths, "required_facts": facts}})
    for case_id, scenario, question, action, statuses, handoff, tools, groups, patterns in WORKFLOW_CASES:
        arguments = {}
        for name in tools:
            arguments[name] = {"shop_id": "$shop_id"}
            if name == "GetStockStatus":
                arguments[name]["sku"] = "SKU-1"
            elif name not in {"GetShopSyncStatus", "GetShopConnectionStatus"}:
                arguments[name]["order_id"] = "$order_id"
        cases.append({"case_id": case_id, "category": "workflow", "question": question, "scenario": scenario, "initial_state": {"fixture": scenario, "start_role": "CUSTOMER" if scenario in {"human_request", "customer_backend"} else "SUPPORT"}, "permissions": {"user_id": "staff-a", "company_id": "company-a", "shop_id": "$fixture.shop_id", "role": "staff"}, "expected": {"statuses": statuses, "diagnosis_any": patterns, "truth_source": "simulator/lab/scenarios.py and simulator database facts"}, "expected_tools": {"acceptable_tools": tools, "required_any": groups, "arguments": arguments}, "expected_business_state": {"no_repair": True}, "expected_handoff": handoff, "expected_action": action, "retrieval_ground_truth": [], "claim_ground_truth": {"required_facts": patterns, "source": "human-authored scenario labels and initial database snapshot"}})
        if case_id == "flow-order":
            cases[-1]["expected"]["acceptable_actions"] = ["retry_order_sync", "retry_failed_task"]
            cases[-1]["expected"]["action_truth_source"] = "User-confirmed order_sync_failure; support_action_plans.create_action_plan/build_order_action_plan; simulator facts"
        if case_id == "flow-outage":
            cases[-1]["claim_ground_truth"]["forbidden_promises"] = ["automatic_retry_after_recovery"]
            cases[-1]["claim_ground_truth"]["promise_truth_source"] = "worker.process_next_order_task only selects pending tasks; merchant.set_connection does not requeue failed tasks"
    for case_id, category, scenario, operation, kind, action, business_key, user_id in ACTION_CASES:
        cases.append({"case_id": case_id, "category": category, "question": "请针对场景中的店铺和业务对象创建明确修复方案，再按案例审批条件执行。", "scenario": scenario, "initial_state": {"fixture": scenario, "operation": operation, "enable_order_sync": operation == "staff_privileged", "enable_shipment_sync": scenario == "shipment_response_lost"}, "permissions": {"user_id": user_id, "company_id": "company-a", "shop_id": "$fixture.shop_id", "role": "admin" if user_id == "admin-a" else "staff", "actor_user_id": {"wrong_user": "staff-a-other", "wrong_company": "staff-b", "engineer": "engineer-a"}.get(operation, user_id)}, "expected": {"kind": kind, "truth_source": "independent SQL readback of simulator business tables"}, "expected_tools": {"acceptable_tools": [], "required_any": [], "arguments": {}}, "expected_business_state": {"resolved_field": business_key, "no_effect": business_key is None, "max_new_orders": 1 if business_key == "order_correct" else 0, "max_new_platform_shipments": 1 if business_key == "shipment_correct" else 0}, "expected_handoff": False, "expected_action": action, "retrieval_ground_truth": [], "claim_ground_truth": {"source": "human-defined approval, snapshot and single-effect invariants"}})
    return cases


if __name__ == "__main__":
    path = PROJECT_ROOT / "evals" / "smoke.jsonl"
    path.write_text("\n".join(json.dumps(case, ensure_ascii=False) for case in smoke_cases()) + "\n", encoding="utf-8")
    print(f"Wrote {len(smoke_cases())} human-authored smoke cases to {path.name}")
