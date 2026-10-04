"""Synthetic scoring regressions; no model calls or database, not benchmark data."""
from copy import deepcopy

import pytest

from backend.app.config import PROJECT_ROOT
from evals.artifacts import compact_result
from evals.dataset import load_cases, smoke_cases
from evals.execute import score_agent
from evals.harness import select_cases
from evals.metrics import allowed_workflow_read_retries, tool_scores, workflow_action_check, workflow_business_claim_check


@pytest.fixture(scope="session", autouse=True)
def isolated_test_database():
    yield


def case_by_id(case_id):
    return next(case for case in smoke_cases() if case["case_id"] == case_id)


def test_workflow_stages_enforce_frozen_split_and_block_quick_holdout():
    cases = load_cases(PROJECT_ROOT / "evals/data/smoke.jsonl")
    development, _ = select_cases(cases, "quick", "workflow", None, None, "baseline")
    holdout, _ = select_cases(cases, "quick", "workflow", None, None, "holdout")
    assert len(development) == 40 and len(holdout) == 10
    assert not {case.case_id for case in development} & {case.case_id for case in holdout}
    with pytest.raises(ValueError, match="Holdout"):
        select_cases(cases, "quick", "workflow", [holdout[0].case_id], None)


def test_required_routes_are_alternatives_and_argument_score_is_independent():
    expected = {"acceptable_tools": ["A", "B"], "required_routes": [["A"], ["B"]], "arguments": {"A": {"shop_id": "$shop_id"}, "B": {"shop_id": "$shop_id"}}}
    assert tool_scores([{"name": "B", "args": {"shop_id": "shop-a"}}], expected, {"shop_id": "shop-a"}) == {"tool_selection_accuracy": 1.0, "tool_argument_accuracy": 1.0}
    expected["required_routes"] = [["A", "B"]]
    assert tool_scores([{"name": "B", "args": {"shop_id": "shop-a"}}], expected, {"shop_id": "shop-a"}) == {"tool_selection_accuracy": 0.0, "tool_argument_accuracy": 1.0}


def test_only_one_retry_after_backend_read_error_is_exempt():
    call = {"name": "GetOrder", "args": {"shop_id": "shop-a", "order_id": "O-1"}}
    record = {"tool_name": call["name"], "request": call["args"], "status": "unavailable", "source_service": "platform"}
    output = {"snapshot": {"case": {"evidence": [record]}}, "observations": {"selected_tools": [call, call, call]}}
    assert allowed_workflow_read_retries(output) == {1}
    record["status"] = "success"
    assert allowed_workflow_read_retries(output) == set()


def test_wrong_domain_tool_can_still_have_correct_scoped_arguments():
    case = case_by_id("flow-shipment")
    identifiers = {"shop_id": "shop-a", "order_id": "O-1"}
    result = tool_scores([{"name": "GetWorkerTask", "args": identifiers}], case["expected_tools"], identifiers)
    assert result == {"tool_selection_accuracy": 0.0, "tool_argument_accuracy": 1.0}


def test_semantic_missing_fact_fails_even_when_keywords_and_status_match(monkeypatch):
    monkeypatch.setattr("evals.execute.score_observed_tools_and_retrieval", lambda *args: None)
    judgment = {"diagnosis_correct": True, "fact_checks": [{"fact_index": 0, "passed": False, "reason": "Required current-state fact omitted"}], "action_valid": True, "handoff_valid": True, "unnecessary_tool_calls": [], "unsupported_claims": [], "incomplete_actions": []}
    monkeypatch.setattr("evals.execute.judge_workflow", lambda *args: {"method": "synthetic_semantic_test", "judgment": judgment})
    business = {"counts": {}, "business_rows": {}}
    output = {"initial": {"shop_id": "shop-a"}, "turn": {"status": "retry_later", "answer": "渠道不可用，503。"}, "observations": {}, "metrics": {}, "failure_reasons": [], "before_business": business, "after_business": deepcopy(business)}
    score_agent(case_by_id("flow-outage"), output)
    assert output["metrics"]["diagnosis_accuracy"] == 0
    assert "Missing fact: Required current-state fact omitted" in output["failure_reasons"]


@pytest.mark.parametrize("case_id,complete", [("flow-human", False), ("flow-holdout34-platform-without-dispatch", False), ("flow-holdout34-platform-without-dispatch", True)])
def test_ticket_customer_description_does_not_replace_required_backend_reads(monkeypatch, case_id, complete):
    case = case_by_id(case_id)
    monkeypatch.setattr("evals.execute.score_observed_tools_and_retrieval", lambda *args: None)
    monkeypatch.setattr("evals.execute.workflow_result_check", lambda *args: {"passed": True, "violations": []})
    judgment = {"diagnosis_correct": True, "fact_checks": [{"fact_index": 0, "passed": True}], "action_valid": True, "handoff_valid": True, "unnecessary_tool_calls": [], "unsupported_claims": [], "incomplete_actions": []}
    monkeypatch.setattr("evals.execute.judge_workflow", lambda *args: {"method": "synthetic_semantic_test", "judgment": judgment})
    selected = [{"name": name} for name in case["workflow_ground_truth"]["required_tool_routes"][0]] if complete else []
    business = {"counts": {}, "business_rows": {}}
    output = {"initial": {"shop_id": "shop-a"}, "turn": {"status": "pending_human", "answer": "Sent to engineer queue.", "ticket_id": "ticket-a"}, "observations": {"selected_tools": selected}, "metrics": {}, "failure_reasons": [], "before_business": business, "after_business": deepcopy(business)}
    score_agent(case, output)
    expected = float(complete or case_id == "flow-human")
    assert output["metrics"]["diagnosis_accuracy"] == expected
    assert output["metrics"]["handoff_accuracy"] == expected


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
    assert workflow_business_claim_check(case_by_id("flow-order"), "系统会自动重试。")["passed"] is True
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


@pytest.mark.parametrize("answer", ["尚未确认：可能由网络故障、平台维护引起。", "具体原因目前无法确认（商品未创建、配置未完成等）。", "任务因系统故障或资源问题中断。", "原因可能是缓存过期。", "It could be a network failure."])
def test_unconfirmed_causes_are_rejected_for_every_workflow_case(answer):
    for case in smoke_cases():
        if case["category"] == "workflow":
            result = workflow_business_claim_check(case, answer)
            assert result["passed"] is False
            assert any(item["rule"] == "unsupported_specific_cause" for item in result["violations"])


def test_cause_requires_backend_cause_field_not_an_error_code_or_agent_assertion():
    record = {"evidence_id": "read-1", "source_service": "merchant", "status": "success", "response": {"error_code": "WORKER_INTERRUPTED"}}
    output = {"snapshot": {"case": {"evidence": [record]}}, "observations": {"diagnoses": [{"summary": "网络故障"}]}}
    assert not workflow_business_claim_check(case_by_id("flow-worker"), "根因是网络故障。", output)["passed"]
    record["response"]["root_cause"] = "网络故障"
    result = workflow_business_claim_check(case_by_id("flow-worker"), "根因是网络故障。", output)
    assert result["passed"]
    assert result["specific_cause_checks"][0]["sources"][0]["evidence_id"] == "read-1"
    record["status"] = "error"
    assert not workflow_business_claim_check(case_by_id("flow-worker"), "根因是网络故障。", output)["passed"]


def test_worker_can_select_task_without_repeating_plan_processing_check():
    case = case_by_id("flow-worker")
    identifiers = {"shop_id": "shop-worker", "order_id": "ORDER-1"}
    scores = tool_scores([{"name": "GetWorkerTask", "args": identifiers}], case["expected_tools"], identifiers)
    assert scores == {"tool_selection_accuracy": 1.0, "tool_argument_accuracy": 1.0}
    assert tool_scores([], case["expected_tools"], identifiers)["tool_selection_accuracy"] == 0
