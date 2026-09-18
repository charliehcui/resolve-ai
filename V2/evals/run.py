import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.app.config import PROJECT_ROOT
from backend.app.report import estimate_cost, summarize_results
from simulator.lab.checks import check_case

Evaluator = Callable[[dict[str, Any], str], dict[str, Any]]
HOLDOUT_PATH = PROJECT_ROOT / "evals" / "holdout.jsonl"
HOLDOUT_SUMMARY_PATH = PROJECT_ROOT / "evals" / "holdout-summary.json"


class EvalCase(BaseModel):
    case_id: str
    category: Literal["qa", "order", "shipment", "stock", "ticket"]
    question: str
    scenario: str | None = None
    expected: dict[str, Any]
    comparison_groups: list[str] = Field(default_factory=list)


def load_cases(path: Path, allow_holdout: bool = False) -> list[EvalCase]:
    if path.resolve() == HOLDOUT_PATH.resolve() and not allow_holdout:
        raise PermissionError("Frozen Holdout content is locked until the full benchmark is explicitly approved")
    cases = [EvalCase.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Dataset contains duplicate case IDs")
    return cases


def verify_holdout_integrity() -> dict[str, Any]:
    summary = json.loads(HOLDOUT_SUMMARY_PATH.read_text(encoding="utf-8"))
    actual_hash = hashlib.sha256(HOLDOUT_PATH.read_bytes()).hexdigest()
    return {"status": summary["status"], "case_count": summary["case_count"], "expected_sha256": summary["sha256"], "actual_sha256": actual_hash, "matches": actual_hash == summary["sha256"]}


def validate_dev_dataset(cases: list[EvalCase], config: dict[str, Any]) -> dict[str, Any]:
    by_id = {case.case_id: case for case in cases}
    missing_sources = []
    for case in cases:
        source = case.expected.get("source")
        if source and not (PROJECT_ROOT / source).exists():
            missing_sources.append({"case_id": case.case_id, "source": source})
    planned_ids = [case_id for experiment in config["experiments"] for case_id in experiment["case_ids"]]
    return {
        "case_count": len(cases),
        "category_counts": {category: sum(case.category == category for case in cases) for category in ("qa", "order", "shipment", "stock", "ticket")},
        "missing_sources": missing_sources,
        "missing_planned_cases": sorted(set(planned_ids) - set(by_id)),
        "comparison_case_count": len(set(planned_ids)),
        "valid": len(cases) == 50 and not missing_sources and not (set(planned_ids) - set(by_id)),
    }


def build_comparison_runs(cases: list[EvalCase], config: dict[str, Any]) -> list[dict[str, Any]]:
    by_id = {case.case_id: case for case in cases}
    runs = []
    for experiment in config["experiments"]:
        for variant in experiment["variants"]:
            for repeat in range(1, experiment["repeat"] + 1):
                for case_id in experiment["case_ids"]:
                    case = by_id[case_id]
                    runs.append({"run_number": len(runs) + 1, "experiment": experiment["name"], "variant": variant, "repeat": repeat, "case_id": case_id, "category": case.category})
    return runs


def run_cases(cases: list[EvalCase], variant: str, repeat: int, evaluator: Evaluator) -> list[dict[str, Any]]:
    results = []
    for iteration in range(1, repeat + 1):
        for case in cases:
            started = time.perf_counter()
            try:
                output = evaluator(case.model_dump(), variant)
                status = "completed"
                error_type = None
            except Exception as error:  # noqa: BLE001 - every evaluator failure must remain in the benchmark denominator
                output = {"behavior": None, "safety": {"false_success": False}, "usage": {}}
                status = "error"
                error_type = type(error).__name__
            results.append({"case_id": case.case_id, "category": case.category, "variant": variant, "repeat": iteration, "status": status, "error_type": error_type, "latency_ms": int((time.perf_counter() - started) * 1000), "usage": output.get("usage", {}), "check": check_case(case.model_dump(), output)})
    return results


def dry_run(config: dict[str, Any], cases: list[EvalCase]) -> dict[str, Any]:
    by_id = {case.case_id: case for case in cases}
    selected = [by_id[case_id] for case_id in config["dry_run_case_ids"]]

    def validate_only(case: dict[str, Any], _: str) -> dict[str, Any]:
        return {"dry_run": True, "usage": {}, "prepared_fields": sorted(case)}

    results = run_cases(selected, "dry_run_validate_only", 1, validate_only)
    return {"mode": "dry_run_validate_only", "external_calls": 0, "model_calls": 0, "results": results, "summary": summarize_results(results)}


def profile_for(variant: dict[str, Any], category: str) -> dict[str, Any]:
    return variant["profiles"].get(category) or variant["profiles"]["default"]


def add_estimate(total: dict[str, float], profile: dict[str, Any], multiplier: int) -> None:
    for provider, count in profile["calls"].items():
        total[f"{provider}_calls"] += float(count) * multiplier
    for name, count in profile["tokens"].items():
        total[name] += float(count) * multiplier
    total["seconds"] += float(profile["seconds"]) * multiplier
    total["runs"] += multiplier


def estimate_benchmark(cases: list[EvalCase], config: dict[str, Any], prices: dict[str, Any]) -> dict[str, Any]:
    by_id = {case.case_id: case for case in cases}
    comparison = {"runs": 0.0, "groq_calls": 0.0, "google_calls": 0.0, "embedding_calls": 0.0, "groq_input": 0.0, "groq_output": 0.0, "google_input": 0.0, "google_output": 0.0, "embedding_input": 0.0, "seconds": 0.0}
    for experiment in config["experiments"]:
        for variant in experiment["variants"].values():
            for case_id in experiment["case_ids"]:
                add_estimate(comparison, profile_for(variant, by_id[case_id].category), experiment["repeat"])
    sections = {"comparison_176": comparison}
    for name in ("holdout", "external_acceptance"):
        section = config[name]
        total = {key: 0.0 for key in comparison}
        add_estimate(total, section["average_per_run"], section["runs"])
        sections[name] = total
    for section in sections.values():
        section["cost"] = estimate_cost(section, prices)
        section["minutes"] = round(section.pop("seconds") / 60, 2)
    combined = {key: sum(section[key] for section in sections.values()) for key in ("runs", "groq_calls", "google_calls", "embedding_calls", "groq_input", "groq_output", "google_input", "google_output", "embedding_input", "minutes")}
    combined["cost"] = estimate_cost(combined, prices)
    combined["recommended_budget_with_25_percent_reserve_usd"] = round((combined["cost"]["total_estimate_usd"] or combined["cost"]["known_chat_cost_usd"]) * 1.25, 2)
    return {"assumption": "Token and latency values are conservative planning estimates, not observed benchmark results.", "sections": sections, "combined": combined}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
