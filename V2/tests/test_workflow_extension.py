"""Fixture and split regressions. Synthetic setup data only, no LLM evaluation."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from backend.app.config import PROJECT_ROOT
from evals.dataset import load_cases, smoke_cases
from evals.harness import free_port, select_cases, validate_smoke
from evals.scenarios import seed_case


@pytest.fixture(scope="session", autouse=True)
def isolated_test_database():
    yield


def test_dataset_split_is_complete_and_quick_does_not_select_holdout():
    cases = load_cases(PROJECT_ROOT / "evals/smoke.jsonl")
    assert validate_smoke(cases)["valid"]
    assert len(cases) == 83
    assert sum(case.category == "workflow" for case in cases) == 50
    assert sum(case.expected.get("split") == "development" for case in cases) == 17
    assert sum(case.expected.get("split") == "holdout" for case in cases) == 10
    selected, _ = select_cases(cases, "quick", "workflow", None, None)
    assert sum(case.expected.get("split") == "regression" for case in cases) == 23
    assert len(selected) == 5
    assert all(case.expected.get("split") == "development" for case in selected)
    assert all(case.expected.get("cohort") == "workflow-30" for case in selected)
    from evals.dataset import EvalCase
    assert [case.model_dump() for case in cases] == [EvalCase.model_validate(case).model_dump() for case in smoke_cases()]


def test_workflow_freeze_rejects_changed_labels_and_fixture_definitions(monkeypatch):
    from copy import deepcopy

    from evals.dataset import validate_workflow_cases

    cases = load_cases(PROJECT_ROOT / "evals/smoke.jsonl")
    assert validate_workflow_cases(cases)["development_regression"] == 40
    changed = deepcopy(cases)
    next(case for case in changed if case.category == "workflow").question += " changed"
    with pytest.raises(AssertionError, match="Frozen Workflow Dataset changed"):
        validate_workflow_cases(changed)
    monkeypatch.setattr("evals.dataset.FROZEN_FIXTURE_HASH", "invalid-fixture-hash")
    with pytest.raises(AssertionError, match="Frozen fixture definitions changed"):
        validate_workflow_cases(cases)


def test_workflow_freeze_has_action_dependent_routes_and_only_fresh_holdout():
    cases = load_cases(PROJECT_ROOT / "evals/smoke.jsonl")
    workflow = {case.case_id: case for case in cases if case.category == "workflow"}
    assert workflow["flow-order"].workflow_ground_truth["required_tool_routes"] == [["GetOrder", "GetOrderProcessRecords"], ["GetWorkerTask"]]
    assert workflow["flow-holdout34-platform-without-dispatch"].expected["split"] == "regression"
    assert all(case.expected["model_execution"] == "never_executed" for case in workflow.values() if case.expected["split"] == "holdout")


@pytest.mark.parametrize("handoff", [False, True])
def test_read_failure_permits_wait_or_handoff_without_claiming_a_business_result(monkeypatch, handoff):
    from evals.execute import score_agent

    case = next(case for case in smoke_cases() if case["case_id"] == "flow-dev-read-failure")
    monkeypatch.setattr("evals.execute.score_observed_tools_and_retrieval", lambda *args: None)
    business = {"counts": {}, "business_rows": {}}
    turn = {"status": "pending_human" if handoff else "retry_later", "answer": "处理记录查询失败，业务状态目前无法确认。", "ticket_id": "ticket-1" if handoff else None}
    output = {"turn": turn, "snapshot": {"ticket": {"ticket_id": "ticket-1"} if handoff else None}, "observations": {}, "metrics": {}, "failure_reasons": [], "before_business": business, "after_business": business}
    score_agent(case, output)
    assert output["metrics"]["handoff_accuracy"] == 1
    assert output["metrics"]["diagnosis_accuracy"] == 1
    output["turn"]["status"] = "diagnosed"
    score_agent(case, output)
    assert output["metrics"]["diagnosis_accuracy"] == 0


def test_unpaid_fixture_supplies_real_identifiers_and_rejects_wrong_initial_state(monkeypatch):
    from evals import scenarios
    result = {"task": {"status": "blocked", "error_code": "ORDER_NOT_PAID"}, "merchant_order": None}
    requests = []
    def create_order(*args, **kwargs):
        requests.append(args)
        return result
    monkeypatch.setattr(scenarios.scenarios, "create_order", create_order)
    case = {"case_id": "test-unpaid", "scenario": "order_unpaid"}
    initial = seed_case(case)
    assert initial["shop_id"] in initial["message"] and initial["order_id"] in initial["message"]
    assert requests[0][-1] == "unpaid"
    result["task"]["error_code"] = None
    with pytest.raises(AssertionError):
        seed_case(case)


@pytest.mark.parametrize("answer, correct", [("等待买家完成支付后系统会自动处理。", False), ("付款后系统会自动导入。", False), ("It will be processed automatically after payment.", False), ("付款后需要重新检查；不能承诺系统会自动处理。", True), ("订单未付款，当前任务 blocked；请先完成付款再重新检查。", True)])
def test_blocked_task_cannot_promise_automatic_processing(answer, correct):
    from evals.metrics import workflow_business_claim_check

    case = next(case for case in smoke_cases() if case["case_id"] == "flow-dev-unpaid")
    assert workflow_business_claim_check(case, answer)["passed"] is correct


@pytest.mark.parametrize("primary_status, retry_status, supported, allowed", [(0, 0, True, True), (-2, 0, True, True), (-2, -2, True, False), (-2, 0, False, False)])
def test_unavailable_primary_requires_verified_recovery_and_prices(monkeypatch, tmp_path, primary_status, retry_status, supported, allowed):
    from types import SimpleNamespace
    from evals import budget

    settings = SimpleNamespace(openrouter_model="model", openrouter_provider="primary", openrouter_retry_provider="retry", openrouter_fallback_model=None, openrouter_fallback_provider=None)
    parameters = ["tools", "response_format", "structured_outputs"] if supported else ["tools"]
    endpoints = [{"tag": tag, "status": status, "supported_parameters": parameters, "provider_name": tag, "pricing": {"prompt": "0.000001", "completion": "0.000002"}} for tag, status in [("primary", primary_status), ("retry", retry_status)]]
    monkeypatch.setattr(budget, "get_settings", lambda: settings)
    monkeypatch.setattr(budget, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(budget.httpx, "get", lambda *args, **kwargs: httpx.Response(200, json={"data": {"endpoints": endpoints}}, request=httpx.Request("GET", "https://example.test")))
    if not allowed:
        with pytest.raises(RuntimeError):
            budget.refresh_prices()
        return
    snapshot = budget.refresh_prices()
    assert snapshot["routing_tag"] == "primary"
    assert snapshot["retry_endpoint"]["routing_tag"] == "retry"
    assert snapshot["prompt"] > 0 and snapshot["completion"] > 0
    assert snapshot["endpoint"]["status"] == primary_status


def test_read_fault_is_armed_after_successful_seed_and_only_for_processing_path(monkeypatch):
    from evals import scenarios
    monkeypatch.setattr(scenarios.scenarios, "create_order", lambda *args, **kwargs: {"task": {"status": "completed"}, "merchant_order": {"merchant_order_id": "order-test"}})
    monkeypatch.setattr(scenarios.scenarios, "user_headers", lambda: {})
    monkeypatch.setattr(scenarios.httpx, "get", lambda *args, **kwargs: httpx.Response(200))
    faults = []
    monkeypatch.setattr(scenarios, "arm_fault", lambda mode, path: faults.append((mode, path)))
    initial = seed_case({"case_id": "test-read-failure", "scenario": "merchant_read_failure"})
    assert faults == [("read_unavailable", f"/internal/process-records/{initial['order_id']}?shop_id=shop-a")]


def test_read_fault_is_persistent_and_does_not_affect_other_paths():
    port = free_port()
    command = [sys.executable, "-c", f"from evals.proxy import serve_proxy; serve_proxy({port}, 'http://127.0.0.1:1')"]
    process = subprocess.Popen(command, cwd=PROJECT_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        url = f"http://127.0.0.1:{port}"
        for attempt in range(50):
            try:
                response = httpx.post(url + "/__eval__/arm", json={"mode": "read_unavailable", "path": "/internal/process-records/ORDER-1?shop_id=shop-a"}, timeout=1)
                response.raise_for_status()
                break
            except httpx.RequestError:
                time.sleep(0.05)
        else:
            raise AssertionError("Fault proxy did not start")
        for attempt in range(2):
            assert httpx.get(url + "/internal/process-records/ORDER-1?shop_id=shop-a").status_code == 503
        assert httpx.get(url + "/health").status_code == 502
    finally:
        process.terminate()
        process.wait(timeout=5)
