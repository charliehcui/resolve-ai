"""Fast synthetic checks of the two evaluation modes; no database or model calls."""
import json

import pytest

from backend.app.config import PROJECT_ROOT
from evals.dataset import load_cases
from evals.harness import empty_result, run_evaluation, select_cases, validate_smoke


@pytest.fixture(scope="session", autouse=True)
def isolated_test_database():
    # These tests replace execution and setup; they do not need PostgreSQL.
    yield


@pytest.fixture()
def local_harness(monkeypatch):
    monkeypatch.setenv("EVAL_RUN_ID", "synthetic-before-run")
    monkeypatch.setattr("evals.budget.refresh_prices", dict)
    monkeypatch.setattr("evals.budget.preflight", lambda cases: {})
    monkeypatch.setattr("evals.budget.cost_summary", lambda run_id: {})
    monkeypatch.setattr("evals.harness.prepare_database", lambda *args, **kwargs: {})
    monkeypatch.setattr("evals.harness.start_services", lambda *args: [])


def test_only_quick_and_final_selection():
    cases = load_cases(PROJECT_ROOT / "evals/smoke.jsonl")
    assert validate_smoke(cases)["valid"]
    selected, variants = select_cases(cases, "quick", "workflow", None, None)
    assert len(selected) == 4
    assert {case.category for case in selected} == {"workflow"}
    assert variants == ["hybrid_rerank"]
    selected, variants = select_cases(cases, "final", None, None, None)
    assert len(selected) == 44
    assert sum(len(variants) if case.category == "rag" else 1 for case in selected) == 64
    for mode, category, ids, modes in [("quick", None, None, None), ("quick", "workflow", ["rag-sync"], None), ("quick", "workflow", ["flow-order"] * 6, None), ("quick", "rag", None, ["hybrid", "vector_only"]), ("final", "workflow", None, None), ("dry-run", "workflow", None, None)]:
        with pytest.raises(ValueError):
            select_cases(cases, mode, category, ids, modes)


def test_explicit_full_workflow_selection_preserves_small_defaults():
    cases = load_cases(PROJECT_ROOT / "evals/smoke.jsonl")
    ids = [case.case_id for case in cases if case.category == "workflow"]
    selected, _ = select_cases(cases, "quick", "workflow", ids, None)
    assert len(selected) == 11
    assert [case.case_id for case in selected] == ids
    assert {case.category for case in selected} == {"workflow"}
    defaults, _ = select_cases(cases, "quick", "workflow", None, None)
    assert len(defaults) == 4
    with pytest.raises(ValueError):
        select_cases(cases, "quick", "rag", [case.case_id for case in cases if case.category == "rag"][:6], None)


def test_quick_runs_only_selected_cases_and_keeps_failure_evidence(tmp_path, monkeypatch, local_harness):
    attempts = []

    def execute(case, variant, repeat, number, directory, timeout):
        attempts.append(case["case_id"])
        result = empty_result(case, variant, repeat)
        result.update(status="failed" if number == 2 else "passed", mock_used=True, model_execution="mock_harness_test", before_business={"large_snapshot_marker": True})
        result["failure_reasons"] = ["Synthetic wrong diagnosis"] if number == 2 else []
        return result

    monkeypatch.setattr("evals.harness.execute_case", execute)
    output = tmp_path / "quick"
    report = run_evaluation(PROJECT_ROOT / "evals/smoke.jsonl", category="workflow", output=output)
    assert len(attempts) == 4
    assert report["summary"]["passed"] == 3
    assert report["summary"]["failed"] == 1
    assert report["summary"]["mock_runs"] == 4
    assert not report["summary"]["baseline_eligible"]
    index = json.loads((output / "evaluation_results.json").read_text(encoding="utf-8"))
    assert "large_snapshot_marker" not in json.dumps(index)
    assert len(list((output / "failures").glob("*.json"))) == 1
    assert all(row["raw_artifact_path"] is None for row in index["results"] if row["status"] == "passed")
    assert (output / "summary.md").exists()
    assert json.loads((output / "summary.json").read_text(encoding="utf-8"))["manifest"]["mode"] == "quick"
    assert {path.name for path in output.iterdir()} == {"summary.md", "summary.json", "evaluation_results.json", "failures"}


def test_quick_stops_on_first_error_and_lists_unexecuted_cases(tmp_path, monkeypatch, local_harness):
    attempts = []

    def execute(case, variant, repeat, number, directory, timeout):
        attempts.append(case["case_id"])
        result = empty_result(case, variant, repeat)
        result.update(mock_used=True, model_execution="mock_harness_test")
        result["failure_reasons"] = ["Synthetic judge failure"]
        return result

    monkeypatch.setattr("evals.harness.execute_case", execute)
    report = run_evaluation(PROJECT_ROOT / "evals/smoke.jsonl", category="workflow", output=tmp_path / "error")
    assert len(attempts) == 1
    assert report["summary"]["error"] == 1
    assert report["summary"]["not_executed_runs"] == 3
    assert len(report["summary"]["not_executed_cases"]) == 3
    assert not report["summary"]["baseline_eligible"]


def test_dataset_still_matches_human_authored_source():
    from evals.dataset import EvalCase, smoke_cases

    cases = load_cases(PROJECT_ROOT / "evals/smoke.jsonl")
    assert [case.model_dump() for case in cases] == [EvalCase.model_validate(case).model_dump() for case in smoke_cases()]


def test_cleanup_file_lock_preserves_completed_results_and_records_warning(tmp_path, monkeypatch, local_harness):
    import shutil
    from pathlib import Path

    cleanup = shutil.rmtree
    directories = []

    def locked(directory):
        directories.append(directory)
        raise PermissionError("Synthetic Windows log file lock")

    def execute(case, variant, repeat, number, directory, timeout):
        result = empty_result(case, variant, repeat)
        result.update(status="passed", mock_used=True, model_execution="mock_harness_test")
        return result

    monkeypatch.setattr("evals.harness.execute_case", execute)
    monkeypatch.setattr("evals.harness.shutil.rmtree", locked)
    output = tmp_path / "locked"
    try:
        report = run_evaluation(PROJECT_ROOT / "evals/smoke.jsonl", category="workflow", case_ids=["flow-auth"], output=output)
        assert report["summary"]["passed"] == 1
        assert report["summary"]["error"] == 0
        assert report["manifest"]["cleanup_warning"]["error_type"] == "PermissionError"
        saved = json.loads((output / "summary.json").read_text(encoding="utf-8"))
        assert saved["manifest"]["cleanup_warning"] == report["manifest"]["cleanup_warning"]
        assert "Temporary cleanup warning" in (output / "summary.md").read_text(encoding="utf-8")
        index = json.loads((output / "evaluation_results.json").read_text(encoding="utf-8"))
        assert index["results"][0]["status"] == "passed"
    finally:
        for directory in directories:
            assert Path(directory).resolve().is_relative_to((PROJECT_ROOT / ".local/eval/runs").resolve())
            cleanup(directory)
