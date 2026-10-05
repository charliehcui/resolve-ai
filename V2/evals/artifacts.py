"""Compact case results, failure-only evidence, and benchmark history."""
import json
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from evals.metrics import execution_failure_kind


def compact_result(result: dict) -> dict:
    case = result.get("case", {})
    response = result.get("turn", {})
    if not isinstance(response, dict):
        response = {}
    reasons = result.get("failure_reasons", [])
    accounting = result.get("llm_accounting", [])
    performance = result.get("performance", {})
    accounting_complete = "llm_accounting" in result
    cost = {"accounted_usd": sum(row["charged_cost"] for row in accounting), "actual_usd": sum(row["actual_cost"] or 0 for row in accounting), "application_usd": sum(row["charged_cost"] for row in accounting if row["scope"] == "application"), "judge_usd": sum(row["charged_cost"] for row in accounting if row["scope"] == "judge"), "unknown_calls": sum(row["status"] == "pending_or_unknown" for row in accounting)} if accounting_complete else {"accounted_usd": None, "actual_usd": None, "reason": "Legacy or unexecuted run has no cost ledger evidence"}
    token_usage = {key: performance.get(key) for key in ("input_tokens", "output_tokens", "token_usage_complete")}
    token_usage["total_tokens"] = performance["input_tokens"] + performance["output_tokens"] if performance.get("input_tokens") is not None and performance.get("output_tokens") is not None else None
    business_evidence = {stage: {key: result[stage][key] for key in ("order_correct", "shipment_correct", "stock_correct", "order_count", "shipment_count", "counts") if key in result[stage]} for stage in ("before_business", "side_effect_baseline", "after_business") if isinstance(result.get(stage), dict)}
    row = {"case_id": result["case_id"], "category": result["category"], "variant": result["variant"], "repeat": result.get("repeat", 1), "status": result["status"], "expected_result": case.get("expected"), "actual_result_summary": {"active_role": response.get("active_role"), "status": response.get("status"), "answer_preview": str(response.get("answer", ""))[:160], "phase": result.get("phase"), "model_execution": result.get("model_execution")}, "business_evidence": business_evidence, "metric_contribution": result.get("metrics", {}), "applicable_metrics": result.get("applicable_metrics", []), "latency": {key: value for key, value in performance.items() if "latency" in key}, "call_counts": {key: value for key, value in performance.items() if "call_count" in key}, "token_usage": token_usage, "cost": cost, "scoring_status": result.get("scoring_status"), "error_type": result.get("error_type") or (reasons[0].split(":", 1)[0] if result["status"] in {"error", "timeout"} and reasons else None), "error_summary": [reason[:300] for reason in reasons], "raw_artifact_path": result.get("raw_artifact"), "mock_used": result.get("mock_used", False)}
    if result["category"] == "workflow":
        row["action_check"] = (result.get("diagnosis_scoring") or {}).get("action_check")
        row["business_claim_check"] = result.get("business_claim_check")
        row["actual_result_summary"].pop("answer_preview")
        row["actual_result_summary"]["answer"] = response.get("answer")
        row["task_result_check"] = result.get("task_result_check")
        row["failure_categories"] = result.get("failure_categories", [])
        row["semantic_checks"] = result.get("independent_judge", {}).get("judgment")
    if result['category'] == 'reliability':
        row['reliability_check'] = result.get('reliability_check')
        row['failure_categories'] = result.get('failure_categories', [])
        row['actual_result_summary']['status'] = (result.get('action_details') or {}).get('status')
    if result['category'] == 'safety':
        row['safety_check'] = result.get('safety_check')
        row['actual_result_summary']['status'] = (result.get('action_details') or {}).get('status')
        row['actual_result_summary']['api_statuses'] = [response['http_status'] for response in result.get('api_responses', [])]
    if result["category"] == "rag":
        row["actual_result_summary"]["answer"] = response.get("answer")
        row["retrieval_evidence"] = result.get("retrieval_evidence", [])
        row["retrieval_runs"] = result.get("retrieval_runs", [])
        row["semantic_checks"] = result.get("independent_judge", {}).get("judgment")
        row["no_answer_correct"] = result.get("no_answer_correct")
        row["failure_categories"] = result.get("failure_categories", [])
        if result["status"] == "passed" and row["semantic_checks"]:
            # 保留逐事实的答案原文证据，允许只重评 Judge 而不重跑 Agent。
            row["semantic_checks"] = dict(row["semantic_checks"])
        observations = result.get("observations", {})
        row["answer_pipeline"] = {key: observations.get(key, []) for key in ("draft_claims", "completeness_review", "before_claims", "after_claims", "removed_claims", "citation_checks", "structured_output")}
        row["judge_audit"] = {key: result.get("independent_judge", {}).get(key) for key in ("method", "source_quote_catalog", "raw_grounding_judgment", "raw_coverage_judgment", "structured_output", "reference_normalization", "usage")}
        rubric = result.get("independent_judge", {}).get("rubric")
        if rubric:
            row["evaluation_rubric"] = {key: rubric[key] for key in ("key", "parts", "original_facts", "classification_sees_answer")}
    row["execution_failure_kind"] = execution_failure_kind(result)
    return row


def index_report(manifest: dict, results: list[dict]) -> dict:
    return {"run_id": manifest["run_id"], "summary_path": "summary.json", "results": [compact_result(result) for result in results]}


def save_results(directory: Path, manifest: dict, results: list[dict], evidence_root: Path) -> None:
    """Persist full evidence only for unsuccessful cases. Never export credentials."""
    directory.mkdir(parents=True, exist_ok=True)
    failure_root = directory / "failures"
    failure_root.mkdir(exist_ok=True)
    index = index_report(manifest, results)
    for result, row in zip(results, index["results"], strict=True):
        row["raw_artifact_path"] = None
        if result["status"] not in {"failed", "error", "timeout"}:
            continue
        name = f"{result['case_id']}-{result['variant']}-r{result.get('repeat', 1)}"
        target = failure_root / (name + ".json")
        evidence = dict(result, raw_artifact="failures/" + target.name)
        target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        row["raw_artifact_path"] = "failures/" + target.name
        raw_name = result.get("raw_artifact")
        if raw_name:
            source = (evidence_root / raw_name).resolve()
            if not source.is_relative_to(evidence_root.resolve()):
                raise ValueError("Evidence path leaves the run directory")
            for log_name in ("process.log", "worker.log"):
                log = source.parent / log_name
                if log.exists() and log.stat().st_size:
                    shutil.copy2(log, failure_root / (name + "-" + log_name))
    (directory / "evaluation_results.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def history_entry(manifest: dict, summary: dict, report_path: str, changes: str, issues: str) -> str:
    models = manifest.get("models", {})
    date = datetime.fromisoformat(manifest["started_at_utc"]).astimezone(ZoneInfo("Australia/Sydney")).isoformat()
    lines = [f"## {date} / {manifest['run_id']}", "", f"- Version: `{manifest.get('git_head')}`; dataset SHA256: `{manifest.get('dataset_sha256')}`.", f"- Dataset: {manifest.get('selected_cases')} cases / {manifest.get('planned_runs')} planned runs / {summary['total_runs']} recorded runs.", f"- Model: `{models.get('application', models.get('customer'))}`; Support: `{models.get('support', models.get('application'))}`; Judge: `{models.get('judge', models.get('independent_judge'))}`; fallback: `{models.get('fallback_model')}`.", f"- Status: Passed {summary['passed']} / Failed {summary['failed']} / Error {summary['error']} / Timeout {summary['timeout']}; benchmark eligible: {summary.get('baseline_eligible', False)}.", f"- Changes: {changes}", f"- Known issues: {issues}", f"- Report: [{report_path}]({report_path})", "", "| Metric | Value | Missing evidence runs |", "|---|---:|---:|"]
    for name, value in summary["metrics"].items():
        lines.append(f"| {name} | {value['value']} | {value.get('missing_evidence_runs', 'unknown')} |")
    performance = summary.get("performance", {})
    lines += ["", f"- Application wall-clock P50 / P95: {performance.get('p50_latency_ms')} / {performance.get('p95_latency_ms')} ms; measured runs: {performance.get('latency_measured_runs')}.", f"- Application tokens per run (mean): input={performance.get('input_tokens', {}).get('mean')}, output={performance.get('output_tokens', {}).get('mean')}, total={performance.get('total_tokens', {}).get('mean')}."]
    cost = summary.get("cost", {})
    run_cost = cost.get("run", {})
    lines += [f"- OpenRouter ledger tokens (application + Judge): input={run_cost.get('input_tokens')}, output={run_cost.get('output_tokens')}, total={run_cost.get('total_tokens')}.", f"- OpenRouter cost: confirmed={run_cost.get('actual_usd')} USD; accounted including unknown reserves={run_cost.get('accounted_usd')} USD; cumulative={cost.get('cumulative_accounted_usd')} USD. Other-provider / embedding charges are not covered by this ledger.", "", "Retrieval comparison:", "", "| Mode | Runs | Recall@5 | MRR | Answer Accuracy after |", "|---|---:|---:|---:|---:|"]
    for mode, values in summary.get("retrieval_comparison", {}).items():
        metrics = values["metrics"]
        lines.append(f"| {mode} | {values['total_runs']} | {metrics['recall_at_5']['value']} | {metrics['mrr']['value']} | {metrics['answer_accuracy_after']['value']} |")
    return "\n".join(lines) + "\n\n"


def workflow_history_entry(summary: dict, title: str, stage: str) -> str:
    manifest = summary["manifest"]
    cost = summary.get("cost", {}).get("run", {})
    performance = summary["performance"]
    lines = ["## " + title, "", f"- Run: `{manifest['run_id']}`; cases: {summary['passed']}/{manifest['selected_cases']}; failed: {summary['failed']}; Error: {summary['error']}; Timeout: {summary['timeout']}.", f"- Report: [reports/workflow/{stage}/summary.md](../reports/workflow/{stage}/summary.md)", "", "| Metric | Value |", "|---|---:|"]
    for name in ("task_success_rate", "diagnosis_accuracy", "tool_selection_accuracy", "tool_argument_accuracy", "handoff_accuracy"):
        value = summary["metrics"][name]["value"]
        lines.append(f"| {name} | {value:.2%} |" if value is not None else f"| {name} | unknown |")
    lines += ["", f"- Unsupported Claim / Incomplete Action: {summary['workflow_checks']['unsupported_claims']} / {summary['workflow_checks']['incomplete_actions']}; missing semantic evidence: {summary['workflow_checks']['missing_semantic_evidence_runs']}.", "- Failure categories (cases may have several): " + json.dumps(summary["workflow_checks"]["failure_categories"]), f"- Application latency P50 / P95: {performance['p50_latency_ms']} / {performance['p95_latency_ms']} ms.", f"- Tokens (application + Judge, known usage): {cost.get('total_tokens')}; application mean: {performance['total_tokens']['mean']}; missing application usage: {performance['total_tokens']['missing_runs']} runs.", f"- Cost (application + Judge): ${cost.get('actual_usd')}; accounted including unknown reserves: ${cost.get('accounted_usd')}; unknown reserve: ${cost.get('unknown_reserve_usd')}; fallback calls: {cost.get('fallback_count')}.", ""]
    return "\n".join(lines) + "\n"
