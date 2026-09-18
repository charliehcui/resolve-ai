from copy import deepcopy

import pytest

from backend.app.config import PROJECT_ROOT
from backend.app.report import estimate_cost, summarize_results
from evals.run import HOLDOUT_PATH, build_comparison_runs, dry_run, estimate_benchmark, load_cases, load_json, validate_dev_dataset, verify_holdout_integrity


def evaluation_inputs():
    cases = load_cases(PROJECT_ROOT / "evals" / "dev.jsonl")
    config = load_json(PROJECT_ROOT / "evals" / "experiments.json")
    prices = load_json(PROJECT_ROOT / "evals" / "price_config.json")
    return cases, config, prices


def test_dataset_and_comparison_denominators() -> None:
    cases, config, _ = evaluation_inputs()
    validation = validate_dev_dataset(cases, config)
    assert validation == {"case_count": 50, "category_counts": {"qa": 20, "order": 12, "shipment": 12, "stock": 3, "ticket": 3}, "missing_sources": [], "missing_planned_cases": [], "comparison_case_count": 20, "valid": True}
    runs = build_comparison_runs(cases, config)
    assert len(runs) == 176
    assert sum(item["experiment"] == "retrieval" for item in runs) == 48
    assert sum(item["experiment"] == "investigation" for item in runs) == 48
    assert sum(item["experiment"] == "roles" for item in runs) == 80


def test_holdout_is_locked_and_checksum_matches_without_loading_cases() -> None:
    with pytest.raises(PermissionError, match="locked"):
        load_cases(HOLDOUT_PATH)
    integrity = verify_holdout_integrity()
    assert integrity["case_count"] == 30
    assert integrity["matches"] is True


def test_dry_run_is_small_and_explicitly_not_scored() -> None:
    cases, config, _ = evaluation_inputs()
    result = dry_run(config, cases)
    assert result["external_calls"] == 0
    assert result["model_calls"] == 0
    assert result["summary"]["runs"] == 3
    assert result["summary"]["scored_runs"] == 0
    assert result["summary"]["not_scored"] == 3


def test_estimate_counts_every_planned_section_and_uses_price_proxy_openly() -> None:
    cases, config, prices = evaluation_inputs()
    estimate = estimate_benchmark(cases, config, prices)
    assert estimate["sections"]["comparison_176"]["runs"] == 176
    assert estimate["sections"]["holdout"]["runs"] == 90
    assert estimate["sections"]["external_acceptance"]["runs"] == 9
    assert estimate["combined"]["runs"] == 275
    assert estimate["combined"]["groq_calls"] > 0
    assert estimate["combined"]["google_calls"] > 0
    assert estimate["combined"]["cost"]["embedding_price_is_proxy"] is True
    assert estimate["combined"]["recommended_budget_with_25_percent_reserve_usd"] > estimate["combined"]["cost"]["total_estimate_usd"]


def test_missing_price_is_not_reported_as_zero() -> None:
    _, _, prices = evaluation_inputs()
    no_embedding_price = deepcopy(prices)
    no_embedding_price["embedding"]["input_per_million_tokens"] = None
    no_embedding_price["embedding"]["estimate_proxy_per_million_tokens"] = None
    cost = estimate_cost({"groq_input": 0, "groq_output": 0, "google_input": 0, "google_output": 0, "embedding_input": 100}, no_embedding_price)
    assert cost["embedding_cost_usd"] is None
    assert cost["total_estimate_usd"] is None


def test_report_keeps_failures_in_denominator() -> None:
    results = [
        {"status": "completed", "latency_ms": 10, "usage": {}, "check": {"status": "passed"}},
        {"status": "completed", "latency_ms": 20, "usage": {}, "check": {"status": "failed"}},
        {"status": "error", "latency_ms": 30, "usage": {}, "check": {"status": "failed"}},
    ]
    summary = summarize_results(results)
    assert summary["runs"] == 3
    assert summary["scored_runs"] == 3
    assert summary["passed"] == 1
    assert summary["failed"] == 2
    assert summary["errors"] == 1
