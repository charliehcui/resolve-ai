"""Harness unit tests use explicit synthetic observations, never reported as LLM evaluations."""
from copy import deepcopy

import pytest

from backend.app.config import PROJECT_ROOT
from evals.dataset import load_cases, smoke_cases
from evals.harness import empty_result, validate_smoke
from evals.metrics import claim_scores, retrieval_scores, summarize, tool_scores
from evals.observe import interval_ms
from simulator.lab.checks import check_case


def test_smoke_ground_truth_and_fixtures_are_complete():
    cases = load_cases(PROJECT_ROOT / "evals" / "data" / "smoke.jsonl")
    validation = validate_smoke(cases)
    assert validation["valid"]
    assert validation["categories"] == {"rag": 40, "workflow": 50, "safety": 25, "reliability": 8}
    assert [case.model_dump() for case in cases] == [load_cases_from_dict(case) for case in smoke_cases()]
    broken = deepcopy(cases[0])
    broken.scenario = "not_seedable"
    assert not validate_smoke([broken])["valid"]


def test_rag_dataset_is_frozen_and_rejects_bad_evidence_and_label_changes():
    from evals.dataset import validate_rag_cases

    cases = load_cases(PROJECT_ROOT / "evals/data/smoke.jsonl")
    result = validate_rag_cases(cases)
    assert result["splits"] == {"development": 30, "holdout": 10}
    assert result["unanswerable"] == 4
    assert result["covered_current_sources"] == 22
    assert result["single_source"] == 29 and result["multi_source"] == 11
    changed = deepcopy(cases)
    changed[0].question += " changed"
    with pytest.raises(AssertionError, match="Frozen RAG Dataset changed"):
        validate_rag_cases(changed)
    changed = deepcopy(cases)
    source = changed[0].retrieval_ground_truth[0]
    changed[0].claim_ground_truth["source_evidence"][source] = ["This evidence does not appear in any formal source."]
    with pytest.raises(AssertionError, match="Evidence missing from formal source"):
        validate_rag_cases(changed, check_freeze=False)


def load_cases_from_dict(case):
    from evals.dataset import EvalCase

    return EvalCase.model_validate(case).model_dump()


def test_missing_safety_evidence_cannot_pass_and_actual_citations_are_supported():
    case = {"expected": {"source": "docs/product/test.md", "role": "CUSTOMER"}}
    output = {"citations": [{"source_uri": "docs/product/test.md"}], "active_role": "CUSTOMER"}
    assert check_case(case, output)["status"] == "failed"
    output["safety"] = {name: False for name in ("unauthorized_write", "cross_tenant_read", "duplicate_business_effect", "false_success")}
    assert check_case(case, output)["status"] == "passed"


def test_errors_and_timeouts_remain_in_success_denominator_and_adverse_rates_are_unknown():
    case = next(case for case in smoke_cases() if case["case_id"] == "safe-order")
    passed = empty_result(case, "current", 1)
    passed.update({"status": "passed", "metrics": {"valid_action_completion_rate": 1.0, "false_success_rate": 0.0, "duplicate_business_effect_rate": 0.0}, "performance": {"end_to_end_latency_ms": 10}})
    error = empty_result(case, "current", 1)
    timeout = empty_result(case, "current", 1)
    timeout["status"] = "timeout"
    report = summarize([passed, error, timeout])
    assert report["total_runs"] == 3
    assert report["error"] == report["timeout"] == 1
    assert report["metrics"]["valid_action_completion_rate"]["value"] == pytest.approx(1 / 3)
    assert report["metrics"]["false_success_rate"]["value"] is None
    assert report["metrics"]["false_success_rate"]["missing_evidence_runs"] == 2


def test_retrieval_uses_all_relevant_documents_and_first_relevant_rank():
    assert retrieval_scores(["noise", "b", "a"], ["a", "b", "c"]) == {"recall_at_5": 2 / 3, "mrr": 0.5}
    assert retrieval_scores([], ["a"]) == {"recall_at_5": 0.0, "mrr": 0.0}


def test_acceptable_tool_groups_and_wrong_or_extra_arguments():
    expected = {"acceptable_tools": ["ReadA", "ReadB"], "required_any": [["ReadA", "ReadB"]], "arguments": {"ReadA": {"shop_id": "$shop_id"}, "ReadB": {"shop_id": "$shop_id"}}}
    identifiers = {"shop_id": "shop-a"}
    assert tool_scores([{"name": "ReadB", "args": {"shop_id": "shop-a"}}], expected, identifiers)["tool_selection_accuracy"] == 1
    assert tool_scores([{"name": "ReadA", "args": {"shop_id": "shop-b"}}], expected, identifiers)["tool_argument_accuracy"] == 0
    assert tool_scores([{"name": "ReadA", "args": {"shop_id": "shop-a", "company_id": "forged"}}], expected, identifiers)["tool_argument_accuracy"] == 0
    assert tool_scores([], expected, identifiers)["tool_selection_accuracy"] == 0


def test_claim_comparison_does_not_reward_deleting_supported_claims():
    before = [{"text": "Supported A"}, {"text": "Unsupported B"}, {"text": "Supported C"}]
    judgment = {"claim_checks": [{"index": 0, "supported": True}, {"index": 1, "supported": False}, {"index": 2, "supported": True}], "answer_correct_before": False, "answer_correct_after": False}
    values = claim_scores(before, [before[0]], judgment)
    assert values["unsupported_claim_rate_after"] == {"numerator": 0, "denominator": 1}
    assert values["supported_claim_retention_rate"] == {"numerator": 1, "denominator": 2}
    judgment["claim_checks"].pop()
    with pytest.raises(ValueError, match="every"):
        claim_scores(before, [before[0]], judgment)


def test_parallel_phase_times_use_union_and_percentiles_keep_wall_latency():
    assert interval_ms([(1, 4), (2, 3), (6, 7)]) == 4000
    case = next(case for case in smoke_cases() if case["case_id"] == "safe-order")
    results = []
    for latency in (10, 20, 30):
        result = empty_result(case, "current", 1)
        result["performance"] = {"end_to_end_latency_ms": latency}
        results.append(result)
    report = summarize(results)
    assert report["performance"]["p50_latency_ms"] == 20
    assert report["performance"]["p95_latency_ms"] == 29


def test_observer_distinguishes_structured_output_from_business_tools_and_keeps_missing_usage(tmp_path):
    from uuid import uuid4

    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, LLMResult

    from evals.observe import Observer

    observer = Observer(tmp_path / "observations.json")
    customer_run = uuid4()
    observer.on_chat_model_start({}, [], run_id=customer_run, tags=["eval_customer"])
    customer_message = AIMessage(content="", tool_calls=[{"id": "structured", "name": "CustomerQueryDecision", "args": {"decision": "search"}}], usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12})
    observer.on_llm_end(LLMResult(generations=[[ChatGeneration(message=customer_message)]]), run_id=customer_run)
    assert observer.snapshot()["selected_tools"] == []
    support_run = uuid4()
    observer.on_chat_model_start({}, [], run_id=support_run, tags=["eval_support"])
    support_message = AIMessage(content="", tool_calls=[{"id": "read", "name": "GetOrder", "args": {"shop_id": "shop-a", "order_id": "O-TEST"}}])
    observer.on_llm_end(LLMResult(generations=[[ChatGeneration(message=support_message)]]), run_id=support_run)
    snapshot = observer.snapshot()
    assert snapshot["selected_tools"][0]["name"] == "GetOrder"
    assert snapshot["performance"]["llm_call_count"] == 2
    assert snapshot["performance"]["input_tokens"] is None
    assert (tmp_path / "observations.json").exists()


def test_journal_file_lock_never_changes_application_behavior(tmp_path, monkeypatch):
    from pathlib import Path
    from uuid import uuid4

    from evals.observe import Observer

    def locked_replace(*args, **kwargs):
        raise PermissionError("Synthetic Windows file-lock regression")

    monkeypatch.setattr(Path, "replace", locked_replace)
    observer = Observer(tmp_path / "observations.json")
    observer.on_chat_model_start({}, [], run_id=uuid4(), tags=["eval_support"])
    assert observer.snapshot()["performance"]["llm_call_count"] == 1
    assert observer.snapshot()["journal_errors"][0]["error_type"] == "PermissionError"
