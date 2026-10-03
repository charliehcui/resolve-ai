"""Synthetic scoring regressions; no model calls or database, not benchmark data."""
from copy import deepcopy

import pytest

from evals.artifacts import compact_result
from evals.dataset import smoke_cases
from evals.execute import score_agent
from evals.metrics import workflow_action_check, workflow_business_claim_check


@pytest.fixture(scope="session", autouse=True)
def isolated_test_database():
    yield


def case_by_id(case_id):
    return next(case for case in smoke_cases() if case["case_id"] == case_id)


def order_observation(action):
    source = {"event_id": "event-test", "version": 1, "sku": "SKU-1", "quantity": 1, "amount_minor": 1000, "payment_status": "paid"}
    task = {"task_id": "task-test", "event_id": "event-test", "version": 2, "status": "failed", "attempts": 1, "retryable": True}
    mapping = {"company_id": "company-a", "shop_id": "shop-test", "platform_sku": "SKU-1", "merchant_sku": "MERCHANT-1", "active": True}
    responses = {"GetOrder": source, "GetOrderProcessRecords": {"task_status": "failed", "error_code": "TRANSIENT_PROCESSING_ERROR"}, "GetShopSyncStatus": {"sync_enabled": True}, "GetShopConnectionStatus": {"connection_status": "authorized"}, "GetSkuMapping": mapping}
    if action == "retry_failed_task":
        responses["GetWorkerTask"] = task
    evidence = []
    for name, response in responses.items():
        request = {"shop_id": "shop-test"}
        if name in {"GetOrder", "GetOrderProcessRecords", "GetWorkerTask"}:
            request["order_id"] = "ORDER-TEST"
        if name == "GetSkuMapping":
            request["platform_sku"] = "SKU-1"
        evidence.append({"evidence_id": name, "tool_name": name, "request": request, "response": response, "status": "success", "source_service": "platform" if name == "GetOrder" else "merchant"})
    snapshot = dict(source, merchant_sku="MERCHANT-1")
    if action == "retry_failed_task":
        snapshot["worker_task"] = {field: task[field] for field in ("task_id", "version", "status", "attempts")}
    candidate = {"action_type": action, "evidence_ids": ["GetOrder", "GetOrderProcessRecords"] if action == "retry_order_sync" else ["GetWorkerTask"]}
    plan = {"action_type": action, "status": "proposed", "company_id": "company-a", "shop_id": "shop-test", "external_order_id": "ORDER-TEST", "source_snapshot": snapshot, "evidence_ids": list(responses)}
    business = {"source": source, "orders": [], "shop": {"sync_enabled": True, "connection_status": "authorized"}, "business_rows": {"merchant.sku_mappings": [mapping]}}
    return {"turn": {"action_plan": plan}, "initial": {"shop_id": "shop-test", "order_id": "ORDER-TEST"}, "after_business": business, "snapshot": {"case": {"evidence": evidence}}, "observations": {"diagnoses": [{"investigation_complete": {"recommended_action": candidate}}]}}


@pytest.mark.parametrize("action", ["retry_order_sync", "retry_failed_task"])
def test_order_accepts_each_legal_action_with_its_own_evidence(action):
    assert workflow_action_check(case_by_id("flow-order"), order_observation(action))["passed"]


def test_task_retry_requires_retryable_task_but_order_retry_does_not():
    output = order_observation("retry_failed_task")
    output["snapshot"]["case"]["evidence"][-1]["response"]["retryable"] = False
    assert not workflow_action_check(case_by_id("flow-order"), output)["passed"]
    assert workflow_action_check(case_by_id("flow-order"), order_observation("retry_order_sync"))["passed"]


@pytest.mark.parametrize("problem", ["wrong_scope", "missing_evidence", "unpaid", "inactive_mapping", "task_wrong_event"])
def test_order_rejects_invalid_prerequisites_and_evidence(problem):
    output = order_observation("retry_failed_task")
    if problem == "wrong_scope":
        output["turn"]["action_plan"]["company_id"] = "company-b"
    elif problem == "missing_evidence":
        output["snapshot"]["case"]["evidence"].pop()
    elif problem == "unpaid":
        output["after_business"]["source"]["payment_status"] = "unpaid"
    elif problem == "inactive_mapping":
        output["after_business"]["business_rows"]["merchant.sku_mappings"][0]["active"] = False
    else:
        output["snapshot"]["case"]["evidence"][-1]["response"]["event_id"] = "another-order-event"
    assert not workflow_action_check(case_by_id("flow-order"), output)["passed"]


def test_multiple_actions_are_not_a_global_alias():
    case = deepcopy(case_by_id("flow-order"))
    case["expected"].pop("acceptable_actions")
    assert not workflow_action_check(case, order_observation("retry_failed_task"))["passed"]


@pytest.mark.parametrize("answer", ["渠道恢复后系统会自动重试。", "需要等待渠道恢复后系统自动重试。", "系统将自动重新同步订单。", "The system will automatically retry after recovery.", "Orders are retried automatically.", "不会自动修复，但系统会自动重试。"])
def test_outage_rejects_unsupported_retry_promises(answer):
    assert workflow_business_claim_check(case_by_id("flow-outage"), answer)["passed"] is False


@pytest.mark.parametrize("answer", ["恢复后请重新检查，届时再申请重试。", "不会自动重试，需要重新检查。", "无法确认恢复后是否自动重试。", "没有证据表明恢复后会自动重试。", "自动重试尚未实现。", "The system does not automatically retry.", "Automatic retry is not implemented."])
def test_outage_allows_denials_and_unknowns(answer):
    assert workflow_business_claim_check(case_by_id("flow-outage"), answer)["passed"] is True


def test_claim_rule_is_explicitly_scoped_and_unknown_rules_raise():
    assert workflow_business_claim_check(case_by_id("flow-order"), "系统会自动重试。")["passed"] is None
    case = case_by_id("flow-outage")
    case["claim_ground_truth"]["forbidden_promises"] = ["unknown_rule"]
    with pytest.raises(ValueError):
        workflow_business_claim_check(case, "test")


def test_correct_outage_flow_with_false_promise_fails_diagnosis(monkeypatch):
    monkeypatch.setattr("evals.execute.score_observed_tools_and_retrieval", lambda *args: None)
    business = {"counts": {}, "business_rows": {}}
    output = {"turn": {"status": "retry_later", "answer": "渠道不可用，恢复后系统会自动重试。"}, "observations": {}, "metrics": {}, "failure_reasons": [], "before_business": business, "after_business": deepcopy(business)}
    score_agent(case_by_id("flow-outage"), output)
    assert output["metrics"]["diagnosis_accuracy"] == 0
    assert output["failure_reasons"] == ["Unsupported business promise: 恢复后系统会自动重试"]


def test_workflow_result_keeps_full_answer_and_scoring_evidence():
    answer = "渠道不可用。" * 50 + "恢复后需要重新检查。"
    result = {"case_id": "flow-outage", "category": "workflow", "variant": "current", "status": "passed", "turn": {"answer": answer}, "diagnosis_scoring": {"action_check": {"passed": True}}, "business_claim_check": {"checked": True, "passed": True}}
    row = compact_result(result)
    assert row["actual_result_summary"]["answer"] == answer
    assert "answer_preview" not in row["actual_result_summary"]
    assert row["business_claim_check"]["passed"]
    assert row["action_check"]["passed"]
