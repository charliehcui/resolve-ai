"""Synthetic accounting and artifact tests, never real-model evaluation results."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from evals.budget import BudgetExceeded, calls_for_run, cost_summary, preflight, reserve, settle
from evals.evaluator import index_report


@pytest.fixture
def budget_environment(tmp_path, monkeypatch):
    snapshot = tmp_path / "pricing.json"
    snapshot.write_text(json.dumps({"model": "test-model", "routing_tag": "test-provider", "provider": "Synthetic", "prompt": 0.000000028, "completion": 0.000000056}), encoding="utf-8")
    monkeypatch.setenv("EVAL_COST_LEDGER", str(tmp_path / "costs.sqlite3"))
    monkeypatch.setenv("EVAL_PRICE_SNAPSHOT", str(snapshot))
    monkeypatch.setenv("EVAL_RUN_ID", "synthetic-run")
    monkeypatch.setenv("EVAL_BUDGET_USD", "1.00")
    monkeypatch.setenv("EVAL_STOP_USD", "0.90")
    return tmp_path


def test_actual_cost_survives_parser_failure_and_is_separate_from_estimate(budget_environment):
    call_id = reserve({"model": "test-model", "max_tokens": 4096}, 100, "judge")
    settle(call_id, {"provider": "Synthetic", "id": "generation-test", "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110, "cost": 0.000003}}, 200)
    summary = cost_summary("synthetic-run")
    assert summary["run"]["actual_usd"] == 0.000003
    assert summary["judge"]["total_tokens"] == 110
    assert calls_for_run("synthetic-run")[0]["estimated_cost"] > 0


def test_explicit_low_reasoning_review_is_reserved_with_its_bounded_output(budget_environment):
    call_id = reserve({"model": "test-model", "max_tokens": 6144, "reasoning": {"effort": "low"}}, 100, "application")
    assert calls_for_run("synthetic-run")[0]["id"] == call_id
    with pytest.raises(ValueError, match="bounded limit"):
        reserve({"model": "test-model", "max_tokens": 6144}, 100, "application")


def test_missing_usage_keeps_reservation_and_next_run_cannot_reset_budget(budget_environment, monkeypatch):
    reserve({"model": "test-model", "max_tokens": 4096}, 100, "application")
    first = cost_summary("synthetic-run")
    monkeypatch.setenv("EVAL_RUN_ID", "second-run")
    second = cost_summary("second-run")
    assert second["run"]["accounted_usd"] == 0
    assert second["cumulative_accounted_usd"] == first["run"]["accounted_usd"] > 0
    assert first["run"]["unknown_reserve_usd"] > 0
    assert first["run"]["usage_missing_calls"] == 1


def test_concurrent_reservations_cannot_cross_stop_threshold(budget_environment, monkeypatch):
    monkeypatch.setenv("EVAL_STOP_USD", "0.00030")

    def attempt():
        try:
            return reserve({"model": "test-model", "max_tokens": 4096}, 100, "application")
        except BudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: attempt(), range(4)))
    assert sum(result is not None for result in results) == 1
    assert cost_summary("synthetic-run")["cumulative_accounted_usd"] <= 0.00030


def test_run_preflight_rejects_unaffordable_run_before_calls(budget_environment, monkeypatch):
    monkeypatch.setenv("EVAL_STOP_USD", "0.001")
    with pytest.raises(BudgetExceeded):
        preflight([{"category": "rag"}])
    assert calls_for_run("synthetic-run") == []


def test_rejected_http_request_has_zero_charge_but_ambiguous_http_does_not(budget_environment):
    a = reserve({"model": "test-model", "max_tokens": 4096}, 100, "application")
    settle(a, {"error": {"code": 401}}, 401)
    b = reserve({"model": "test-model", "max_tokens": 4096}, 100, "application")
    settle(b, {}, 500)
    rows = calls_for_run("synthetic-run")
    assert rows[0]["charged_cost"] == 0
    assert rows[1]["charged_cost"] > 0


def test_streaming_or_unbounded_payload_is_blocked_without_call(budget_environment):
    with pytest.raises(ValueError):
        reserve({"model": "test-model", "stream": True, "max_tokens": 4096}, 100, "application")
    with pytest.raises(BudgetExceeded):
        reserve({"model": "test-model", "max_tokens": 4096}, 65536, "application")
    assert calls_for_run("synthetic-run") == []


def test_raw_http_evidence_survives_later_judge_validation_failure(budget_environment, monkeypatch):
    import httpx

    from evals.budget import accounting_hooks

    directory = budget_environment / "llm"
    monkeypatch.setenv("EVAL_RAW_CALL_DIR", str(directory))
    hooks = accounting_hooks("judge")
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions", json={"model": "test-model", "max_tokens": 4096, "messages": [{"role": "user", "content": "Synthetic probe"}]}, headers={"Authorization": "Bearer synthetic-secret"})
    hooks["request"][0](request)
    raw_response = {"choices": [{"message": {"content": "invalid judge quote"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12, "cost": 0.000001}}
    hooks["response"][0](httpx.Response(200, json=raw_response, request=request))
    saved = json.loads(next(directory.glob("*.json")).read_text(encoding="utf-8"))
    assert saved["response"] == raw_response
    assert "synthetic-secret" not in json.dumps(saved)
    assert cost_summary("synthetic-run")["judge"]["actual_usd"] == 0.000001


def test_compact_index_does_not_copy_complete_raw_evidence():
    result = {"case_id": "synthetic", "category": "rag", "variant": "hybrid", "status": "error", "before_business": {"secret_large_data": "raw only"}, "observations": {"claims": "raw only"}, "raw_artifact": "raw/synthetic/raw.json", "failure_reasons": ["SyntheticError: failed"]}
    index = index_report({"run_id": "synthetic"}, [result])
    text = json.dumps(index)
    assert "secret_large_data" not in text
    assert "observations" not in text
    assert index["results"][0]["raw_artifact_path"] == "raw/synthetic/raw.json"


def test_fallback_allowance_blocks_recovery_before_transmission(budget_environment, monkeypatch):
    from backend.app.llm_context import call_context

    monkeypatch.setenv("EVAL_FALLBACK_BUDGET_USD", "0.00025")
    token = call_context.set({"fallback_count": 1, "logical_call_id": "synthetic-logical-call"})
    try:
        with pytest.raises(BudgetExceeded, match="fallback allowance"):
            reserve({"model": "test-model", "max_tokens": 4096}, 100, "application")
    finally:
        call_context.reset(token)
    assert calls_for_run("synthetic-run") == []


def test_earlier_price_estimate_survives_snapshot_change(budget_environment):
    call_id = reserve({"model": "test-model", "max_tokens": 4096}, 100, "application")
    path = budget_environment / "pricing.json"
    price = json.loads(path.read_text(encoding="utf-8"))
    price.update(prompt=1, completion=1)
    path.write_text(json.dumps(price), encoding="utf-8")
    settle(call_id, {"usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}}, 200)
    assert calls_for_run("synthetic-run")[0]["estimated_cost"] == pytest.approx(0.00000336)


def test_concurrent_probes_have_different_price_snapshot_paths(budget_environment, monkeypatch):
    import os

    import httpx

    from evals import budget

    endpoint = {"tag": "test-provider", "status": 0, "provider_name": "Synthetic", "pricing": {"prompt": "0.000000028", "completion": "0.000000056"}, "supported_parameters": ["tools", "response_format", "structured_outputs"]}
    monkeypatch.setattr(budget, "PROJECT_ROOT", budget_environment)
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: httpx.Response(200, request=httpx.Request("GET", url), json={"data": {"endpoints": [endpoint]}}))
    budget.refresh_prices()
    first_path = os.environ["EVAL_PRICE_SNAPSHOT"]
    monkeypatch.setenv("EVAL_RUN_ID", "another-probe")
    budget.refresh_prices()
    assert os.environ["EVAL_PRICE_SNAPSHOT"] != first_path
    assert json.loads(Path(first_path).read_text(encoding="utf-8"))["model"] == "test-model"
