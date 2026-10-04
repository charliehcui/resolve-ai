"""Quick Evaluation reuses fixtures; Final Benchmark uses fresh isolated data."""
import argparse
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
import psycopg
from psycopg import sql

from backend.app.config import PROJECT_ROOT, get_settings, psycopg_url
from evals.dataset import load_cases
from evals.metrics import applicable_metrics, summarize, workflow_failure_categories
from evals.scenarios import SCENARIO_NAMES

CATEGORIES = ("rag", "workflow", "safety", "reliability")
MODES = ("vector_only", "hybrid", "hybrid_rerank")
QUICK_CASES = {
    "rag": ("rag-sync", "rag-mapping", "rag-shipment", "rag-auth", "rag-stock"),
    "workflow": ("flow-dev30-missing-order", "flow-dev30-restored-auth", "flow-dev30-shipment-receipt", "flow-dev30-stock-rule", "flow-dev30-source-conflict"),
    "safety": ("safe-order", "safe-user", "safe-approval", "safe-expired"),
    "reliability": ("recover-submit", "recover-confirm", "recover-lost", "recover-worker"),
}
SERVICE_NAMES = ("merchant-ingest", "merchant-shipment", "warehouse-ingest", "platform-shipment", "platform-stock", "lab-control", "support-read", "support-write")


def code_hashes() -> dict:
    paths = sorted((PROJECT_ROOT / "evals").glob("*.py"))
    for folder, pattern in (("backend/app", "*.py"), ("backend/prompts", "*.md"), ("simulator/services", "*.py"), ("simulator/lab", "*.py"), ("infra/migrations", "*.sql")):
        paths.extend(sorted((PROJECT_ROOT / folder).glob(pattern)))
    rubric_path = PROJECT_ROOT / "evals/optimization/answer-rubric.json"
    if rubric_path.exists():
        paths.append(rubric_path)
    return {path.relative_to(PROJECT_ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def validate_smoke(cases: list) -> dict:
    errors = []
    for case in cases:
        if case.category not in CATEGORIES:
            errors.append(f"{case.case_id}: unsupported evaluation category")
        if case.scenario not in SCENARIO_NAMES or case.initial_state.get("fixture") != case.scenario:
            errors.append(f"{case.case_id}: unsupported simulator fixture")
        if not case.permissions or not all(name in case.permissions for name in ("user_id", "company_id", "shop_id", "role")):
            errors.append(f"{case.case_id}: missing permissions")
        if not case.expected or not case.claim_ground_truth or "acceptable_tools" not in case.expected_tools or case.expected_handoff is None:
            errors.append(f"{case.case_id}: incomplete human labels")
        if case.category == "rag":
            if not case.retrieval_ground_truth or not case.claim_ground_truth.get("required_facts"):
                errors.append(f"{case.case_id}: missing retrieval or claim ground truth")
            for source in case.retrieval_ground_truth:
                if not (PROJECT_ROOT / source).is_file():
                    errors.append(f"{case.case_id}: missing source {source}")
        if case.category in {"safety", "reliability"} and (not case.expected_business_state or not case.expected_action or case.expected.get("kind") not in {"valid", "invalid", "unauthorized", "recovery"}):
            errors.append(f"{case.case_id}: missing business-state labels")
    return {"valid": not errors, "case_count": len(cases), "categories": dict(Counter(case.category for case in cases)), "errors": errors}


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def prepare_database(directory: Path, include_documents: bool, reuse: bool = False) -> dict:
    from backend.app.database import initialize_database
    from evals.runtime import configure_tokens
    from simulator.lab import bootstrap

    parts = urlsplit(psycopg_url(os.environ["DATABASE_URL"]))
    cache_path = PROJECT_ROOT / ".local" / "eval" / "quick" / "environment.json"
    cached = json.loads(cache_path.read_text(encoding="utf-8")) if reuse and cache_path.exists() else None
    database_name = cached["database"] if cached else "resolveai_eval_" + uuid4().hex[:16]
    if not database_name.startswith("resolveai_eval_"):
        raise PermissionError("Evaluation requires an isolated resolveai_eval_* database")
    admin_url = urlunsplit((parts.scheme, parts.netloc, "/postgres", parts.query, parts.fragment))
    with psycopg.connect(admin_url, autocommit=True, connect_timeout=3) as connection:
        exists = connection.execute("SELECT 1 FROM pg_database WHERE datname=%s", (database_name,)).fetchone()
        if not exists:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
            cached = None
    os.environ["DATABASE_URL"] = urlunsplit((parts.scheme, parts.netloc, "/" + database_name, parts.query, parts.fragment))
    os.environ["LANGSMITH_TRACING"] = "false"
    private_directory = cache_path.parent if reuse else PROJECT_ROOT / ".local" / "eval" / "private" / directory.name
    private_directory.mkdir(parents=True, exist_ok=True)
    service_file = private_directory / "service_tokens.json"
    os.environ["EVAL_OTHER_USER_TOKEN"] = cached["other_user_token"] if cached else secrets.token_urlsafe(32)
    if not cached:
        write_json(service_file, {name: secrets.token_urlsafe(32) for name in SERVICE_NAMES})
    os.environ["EVAL_SERVICE_TOKEN_FILE"] = str(service_file)
    get_settings.cache_clear()
    configure_tokens()
    if not cached:
        initialize_database()
    bootstrap.TOKEN_FILE = private_directory / "user_tokens.json"
    bootstrap.SERVICE_TOKEN_FILE = service_file
    if not cached:
        bootstrap.bootstrap()
    documents_ready = bool(cached and cached.get("documents_ready"))
    result = {"database": database_name, "isolated": True, "reused": cached is not None, "documents": "reused" if documents_ready and include_documents else "not_requested"}
    if include_documents and not documents_ready:
        from backend.app.customer_document_ingestion import import_product_documents
        from evals.execute import safe_error

        try:
            result["documents"] = import_product_documents()
            documents_ready = True
        except Exception as error:  # noqa: BLE001 - business evaluations still run if embedding import fails
            result["documents"] = {"status": "error", "error_type": type(error).__name__, "reason": safe_error(error)}
    if reuse:
        write_json(cache_path, {"database": database_name, "other_user_token": os.environ["EVAL_OTHER_USER_TOKEN"], "documents_ready": documents_ready})
    return result


def start_services(directory: Path) -> list[tuple]:
    ports = {name: free_port() for name in ("merchant", "platform", "warehouse", "proxy")}
    os.environ["MERCHANT_URL"] = f"http://127.0.0.1:{ports['proxy']}"
    os.environ["PLATFORM_URL"] = f"http://127.0.0.1:{ports['platform']}"
    os.environ["WAREHOUSE_URL"] = f"http://127.0.0.1:{ports['warehouse']}"
    os.environ["MERCHANT_INGEST_URL"] = os.environ["MERCHANT_URL"] + "/events/orders"
    os.environ["MERCHANT_SHIPMENT_URL"] = os.environ["MERCHANT_URL"] + "/events/shipments"
    processes = []
    try:
        for name, port in ports.items():
            log_path = directory / "private" / f"{name}.log"
            log = log_path.open("w", encoding="utf-8")
            command = [sys.executable, "-m", "evals.runtime", "--service", name, "--port", str(port)]
            if name == "proxy":
                command += ["--upstream", f"http://127.0.0.1:{ports['merchant']}"]
            process = subprocess.Popen(command, cwd=PROJECT_ROOT, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            processes.append((process, log))
        deadline = time.monotonic() + 30
        waiting = set(ports)
        while waiting and time.monotonic() < deadline:
            for name in list(waiting):
                try:
                    response = httpx.get(f"http://127.0.0.1:{ports[name]}/health", timeout=1)
                    if response.status_code == 200:
                        waiting.remove(name)
                except httpx.RequestError:
                    pass
            if waiting:
                time.sleep(0.1)
        if waiting:
            raise RuntimeError("Simulator services did not become ready: " + ", ".join(sorted(waiting)))
        return processes
    except Exception:
        stop_services(processes)
        raise


def stop_services(processes: list[tuple]) -> None:
    for process, log in processes:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        log.close()


def stop_case(process: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
    else:
        import signal

        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def empty_result(case: dict, variant: str, repeat: int) -> dict:
    return {"case_id": case["case_id"], "category": case["category"], "variant": variant, "repeat": repeat, "case": case, "status": "error", "metrics": {}, "applicable_metrics": applicable_metrics(case), "performance": {}, "failure_reasons": [], "model_execution": "not_executed", "mock_used": False}


def execute_case(case: dict, variant: str, repeat: int, number: int, directory: Path, timeout: float) -> dict:
    case_directory = directory / "raw" / f"{number:03d}-{case['case_id']}-{variant}-r{repeat}"
    case_path = case_directory / "case.json"
    raw_path = case_directory / "raw.json"
    write_json(case_path, case)
    log_path = case_directory / "process.log"
    started = time.perf_counter()
    with log_path.open("w", encoding="utf-8") as log:
        environment = dict(os.environ, EVAL_CASE_KEY=case_directory.name, EVAL_CATEGORY=case["category"], EVAL_RAW_CALL_DIR=str(case_directory / "llm"))
        process = subprocess.Popen([sys.executable, "-m", "evals.execute", "--case", str(case_path), "--variant", variant, "--output", str(raw_path)], cwd=PROJECT_ROOT, env=environment, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0, start_new_session=os.name != "nt")
        try:
            process.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            stop_case(process)
            timed_out = True
    result = json.loads(raw_path.read_text(encoding="utf-8")) if raw_path.exists() else empty_result(case, variant, repeat)
    result["repeat"] = repeat
    result["harness_elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
    result["raw_artifact"] = raw_path.relative_to(directory).as_posix()
    if timed_out:
        result["status"] = "timeout"
        result["failure_reasons"].append(f"Hard case timeout after {timeout} seconds; process tree terminated")
        journal_path = case_directory / "observations.json"
        if journal_path.exists():
            result["observations"] = json.loads(journal_path.read_text(encoding="utf-8"))
            result["performance"].update(result["observations"]["performance"])
        if "end_to_end_latency_ms" not in result["performance"]:
            application_start = result.get("application_started_monotonic")
            result["performance"]["end_to_end_latency_ms"] = round((time.perf_counter() - application_start) * 1000, 3) if application_start is not None else None
            result["performance"]["latency_censored"] = application_start is not None
    elif process.returncode:
        result["status"] = "error"
        result["failure_reasons"].append(f"Case process exited {process.returncode}; see process.log")
    if os.getenv("EVAL_RUN_ID"):
        from evals.budget import calls_for_run

        result["llm_accounting"] = [row for row in calls_for_run(os.environ["EVAL_RUN_ID"]) if row["case_id"] == case_directory.name]
    write_json(raw_path, result)
    return result


def summary_with_comparisons(results: list[dict]) -> dict:
    summary = summarize(results)
    workflow = [result for result in results if result["category"] == "workflow"]
    summary["workflow_checks"] = {"failure_categories": dict(Counter(category for result in workflow for category in result.get("failure_categories", []))), "unsupported_claims": sum(len((result.get("business_claim_check") or {}).get("violations", [])) for result in workflow), "incomplete_actions": sum(sum(reason.startswith("Incomplete Action:") for reason in result.get("failure_reasons", [])) + len((result.get("task_result_check") or {}).get("violations", [])) for result in workflow), "semantic_scored_runs": sum(result.get("independent_judge", {}).get("status") == "scored" for result in workflow), "missing_semantic_evidence_runs": sum(result.get("independent_judge", {}).get("status") != "scored" for result in workflow)}
    summary["by_category"] = {name: summarize([result for result in results if result["category"] == name]) for name in CATEGORIES}
    summary["retrieval_comparison"] = {mode: summarize([result for result in results if result["category"] == "rag" and result["variant"] == mode]) for mode in MODES}
    summary["rerank_fallback_runs"] = [{"case_id": result["case_id"], "repeat": result["repeat"], "error": run["rerank_error"]} for result in results for run in result.get("retrieval_runs", []) if run.get("rerank_error")]
    summary["claim_validation_comparison"] = {stage: {name: summary["metrics"][name + "_" + stage] for name in ("answer_accuracy", "unsupported_claim_rate")} for stage in ("before", "after")}
    summary["model_execution_counts"] = dict(Counter(result["model_execution"] for result in results))
    summary["mock_runs"] = sum(result["mock_used"] for result in results)
    summary["judge_error_runs"] = sum(result.get("scoring_status") == "judge_error" for result in results)
    for group in (summary["by_category"], summary["retrieval_comparison"]):
        for values in group.values():
            values.pop("failures", None)
    for failure in summary["failures"]:
        failure["reasons"] = [reason[:300] for reason in failure["reasons"]]
    summary["error_summary"] = dict(Counter(result.get("error_type", result["status"]) for result in results if result["status"] in {"error", "timeout"}))
    if os.getenv("EVAL_RUN_ID"):
        from evals.budget import cost_summary

        summary["cost"] = cost_summary(os.environ["EVAL_RUN_ID"])
        summary["cost"].pop("by_case", None)
    return summary


def render_summary(summary: dict, manifest: dict) -> str:
    if summary["total_runs"] and summary["by_category"]["rag"]["total_runs"] == summary["total_runs"]:
        from evals.rag import render_summary as render_rag_summary
        return render_rag_summary(summary, manifest)
    if manifest.get("workflow_stage") or manifest.get("validation", {}).get("categories") == {"workflow": manifest.get("selected_cases")}:
        return render_workflow_summary(summary, manifest)
    lines = ["# ResolveAI " + ("Final Benchmark" if manifest.get("mode") == "final" else "Quick Evaluation"), "", f"Run: {manifest['run_id']}", f"Dataset cases: {manifest['selected_cases']}; planned runs: {manifest['planned_runs']}", f"Passed {summary['passed']} / Failed {summary['failed']} / Error {summary['error']} / Timeout {summary['timeout']}", "", "All failures remain in eligible denominators. Missing adverse-rate or claim evidence produces null, never an automatic pass.", "", "| Metric | Value | Numerator | Denominator | Missing evidence runs |", "|---|---:|---:|---:|---:|"]
    lines.insert(6, f"Baseline eligible: {summary.get('baseline_eligible')}; not executed: {summary.get('not_executed_runs', 0)}. Partial runs are not complete-dataset baseline scores.")
    cases = summary["case_success"]
    reliability = summary["execution_reliability"]
    if cases["total_cases"]:
        lines.insert(7, f"Case Success (Workflow): {cases['successful_cases']}/{cases['total_cases']} Cases successful; business failed={cases['business_failed_cases']}; execution-only unknown={cases['execution_only_cases']}. Latest completed business result per Case, not best-of-retries.")
    lines.insert(8, f"Execution Reliability: {reliability['completed_executions']}/{reliability['total_executions']} executions completed; Error={reliability['error_executions']}; Timeout={reliability['timeout_executions']}; failures={reliability['by_failure_kind']}; provider call errors including recovered calls={reliability['provider_call_errors']}.")
    for name, value in summary["metrics"].items():
        lines.append(f"| {name} | {value['value']} | {value['numerator']} | {value['denominator']} | {value['missing_evidence_runs']} |")
    lines += ["", "Performance uses application wall time; setup and evaluation judge are excluded. Phase intervals use their wall-clock union, not a sum of parallel durations.", "", "```json", json.dumps(summary["performance"], indent=2), "```", "", "## Retrieval comparison", ""]
    for mode, values in summary["retrieval_comparison"].items():
        lines.append(f"- {mode}: runs={values['total_runs']}, Recall@5={values['metrics']['recall_at_5']['value']}, MRR={values['metrics']['mrr']['value']}, errors={values['error']}, timeouts={values['timeout']}")
    lines += ["", "## Claim validation comparison", "", "```json", json.dumps(summary["claim_validation_comparison"], indent=2), "```", "", "## Failed cases", ""]
    for failure in summary["failures"]:
        lines.append(f"- {failure['case_id']} / {failure['variant']} / repeat {failure['repeat']}: {failure['status']}: {'; '.join(failure['reasons'])}")
    lines += ["", "## Cost", "", "```json", json.dumps(summary.get("cost", {}), indent=2), "```", "", "Application and judge share the primary DeepSeek model and recovery policy. A GLM recovery makes this a mixed-model run; actual model/provider counts are in the cost summary. This is a baseline/engineering comparison, not independent final quality validation.", ""]
    return "\n".join(lines)


def render_workflow_summary(summary: dict, manifest: dict) -> str:
    lines = ["# Workflow " + (manifest.get("workflow_stage") or "Quick Evaluation"), "", "Run: " + manifest["run_id"], f"Cases: {summary['passed']}/{manifest['selected_cases']}; Error {summary['error']}; Timeout {summary['timeout']}; unexecuted {summary['not_executed_runs']}", "", "| Metric | Value |", "|---|---:|"]
    for name in ("task_success_rate", "diagnosis_accuracy", "tool_selection_accuracy", "tool_argument_accuracy", "handoff_accuracy"):
        lines.append(f"| {name} | {summary['metrics'][name]['value']} |")
    lines += ["", "Checks: " + json.dumps(summary["workflow_checks"], ensure_ascii=False), "", "Performance: " + json.dumps(summary["performance"], ensure_ascii=False), "", "Cost (application + semantic judge): " + json.dumps(summary.get("cost", {}).get("run", {})), "", "Same-family semantic judge plus independent simulator readback; no keyword-only success claims.", "", "Failed / Error cases:"]
    for failure in summary["failures"]:
        lines.append(f"- {failure['case_id']}: {failure['status']}: {'; '.join(failure['reasons'])}")
    return "\n".join(lines) + "\n"


def select_cases(cases: list, mode: str, category: str | None, case_ids: list[str] | None, variants: list[str] | None, workflow_stage: str | None = None, rag_stage: str | None = None) -> tuple[list, list[str]]:
    if rag_stage:
        if mode != "quick" or category != "rag" or case_ids or workflow_stage or rag_stage not in {"baseline", "optimized", "holdout"}:
            raise ValueError("RAG stages require the complete frozen split, quick mode and RAG only")
        split = "holdout" if rag_stage == "holdout" else "development"
        selected = [case for case in cases if case.category == "rag" and case.expected.get("split") == split]
        if len(selected) != (10 if split == "holdout" else 30):
            raise ValueError("RAG stages require the frozen 30/10 split")
        selected_modes = variants or ["vector_only" if rag_stage == "baseline" else "hybrid"]
        if len(selected_modes) != 1 or rag_stage == "baseline" and selected_modes != ["vector_only"]:
            raise ValueError("RAG Baseline uses vector_only; other stages use one selected mode")
        return selected, selected_modes
    if workflow_stage:
        if mode != "quick" or category != "workflow" or case_ids or variants:
            raise ValueError("Workflow stages require quick mode, Workflow only, and the complete frozen split")
        if workflow_stage not in {"baseline", "optimized", "holdout"}:
            raise ValueError("Unknown Workflow stage")
        splits = {"holdout"} if workflow_stage == "holdout" else {"development", "regression"}
        selected = [case for case in cases if case.category == "workflow" and case.expected.get("split") in splits]
        if len(selected) != (10 if workflow_stage == "holdout" else 40):
            raise ValueError("Workflow stage requires the frozen 40/10 split")
        return selected, ["hybrid_rerank"]
    if mode not in {"quick", "final"}:
        raise ValueError("Evaluation mode must be quick or final")
    if mode == "final":
        if category is not None or case_ids:
            raise ValueError("Final Benchmark runs the whole dataset; use quick for module selection")
        if variants is not None and (len(variants) != len(MODES) or set(variants) != set(MODES)):
            raise ValueError("Final Benchmark requires all three retrieval modes")
        return cases, variants or list(MODES)
    if category not in CATEGORIES:
        raise ValueError("Quick Evaluation requires one explicit --category")
    wanted = case_ids or list(QUICK_CASES[category])
    limit = 15 if category == "rag" else 11 if category == "workflow" else 5
    if not 1 <= len(wanted) <= limit or len(set(wanted)) != len(wanted):
        raise ValueError(f"Quick Evaluation accepts 1-{limit} distinct {category} cases")
    by_id = {case.case_id: case for case in cases if case.category == category}
    if set(wanted) - set(by_id):
        raise ValueError("Unknown cases or cases outside the selected category")
    if category in {"workflow", "rag"} and any(by_id[case_id].expected.get("split") == "holdout" for case_id in wanted):
        raise ValueError("Frozen Holdout can only run once using the corresponding --*-stage holdout")
    selected_modes = variants or ["hybrid_rerank"]
    if len(selected_modes) != 1:
        raise ValueError("Quick Evaluation uses one retrieval mode; compare modes in separate targeted checks")
    return [by_id[case_id] for case_id in wanted], selected_modes


def run_evaluation(suite: Path, mode: str = "quick", category: str | None = None, case_ids: list[str] | None = None, variants: list[str] | None = None, timeout: float = 180, output: Path | None = None, changes: str = "", workflow_stage: str | None = None, rag_stage: str | None = None) -> dict:
    cases = load_cases(suite)
    validation = validate_smoke(cases)
    if not validation["valid"]:
        raise ValueError(json.dumps(validation, ensure_ascii=False))
    selected, variants = select_cases(cases, mode, category, case_ids, variants, workflow_stage, rag_stage)
    if timeout <= 0 or not variants or not set(variants).issubset(MODES):
        raise ValueError("Invalid timeout or retrieval variants")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    directory = PROJECT_ROOT / ".local" / "eval" / "runs" / run_id
    directory.mkdir(parents=True)
    (directory / "private").mkdir()
    destination = (output or PROJECT_ROOT / "reports" / "workflow" / workflow_stage if workflow_stage else output or PROJECT_ROOT / "reports" / "latest").resolve()
    if rag_stage:
        destination = (output or PROJECT_ROOT / "reports" / "rag" / rag_stage).resolve()
        if (destination / "summary.json").exists():
            raise FileExistsError("Formal RAG stage results are immutable")
        if rag_stage == "holdout":
            optimized = PROJECT_ROOT / "reports" / "rag" / "optimized" / "summary.json"
            if not optimized.exists() or json.loads(optimized.read_text(encoding="utf-8")).get("not_executed_runs"):
                raise ValueError("Finish optimized Development before RAG Holdout")
            with (PROJECT_ROOT / ".local" / "eval" / "rag-holdout-started.json").open("x", encoding="utf-8") as handle:
                json.dump({"run_id": run_id}, handle)
    if workflow_stage and (destination / "summary.json").exists():
        raise FileExistsError("Workflow stage results are immutable; this stage has already run")
    if workflow_stage == "holdout":
        optimized = PROJECT_ROOT / "reports" / "workflow" / "optimized" / "summary.json"
        if not optimized.exists() or json.loads(optimized.read_text(encoding="utf-8")).get("not_executed_runs"):
            raise ValueError("Finish the complete optimized Development run before Holdout")
        marker = PROJECT_ROOT / ".local" / "eval" / "workflow-holdout-started.json"
        with marker.open("x", encoding="utf-8") as handle:
            json.dump({"run_id": run_id, "dataset_sha256": hashlib.sha256(suite.read_bytes()).hexdigest()}, handle)
    runs = [(case.model_dump(), variant, 1) for case in selected for variant in (variants if case.category == "rag" else ["current"])]
    model_names = {"application": os.getenv("OPENROUTER_MODEL"), "judge": os.getenv("OPENROUTER_MODEL"), "provider": os.getenv("OPENROUTER_PROVIDER"), "retry_provider": os.getenv("OPENROUTER_RETRY_PROVIDER"), "fallback_model": os.getenv("OPENROUTER_FALLBACK_MODEL"), "fallback_provider": os.getenv("OPENROUTER_FALLBACK_PROVIDER"), "embedding": os.getenv("EMBEDDING_MODEL"), "reranker": os.getenv("RERANK_MODEL")}
    manifest = {"run_id": run_id, "mode": mode, "started_at_utc": datetime.now(UTC).isoformat(), "suite": suite.name, "dataset_sha256": hashlib.sha256(suite.read_bytes()).hexdigest(), "dataset_cases": len(cases), "selected_cases": len(selected), "planned_runs": len(runs), "validation": validation, "retrieval_modes": variants, "case_timeout_seconds": timeout, "mock_used": False, "models": model_names, "code_hashes": code_hashes(), "source_hashes": {path.relative_to(PROJECT_ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted((PROJECT_ROOT / "docs" / "product").glob("*.md"))}, "scope": "Real API ingress, simulator HTTP, PostgreSQL and worker; application logic unchanged.", "latency_scope": "Application wall clock; setup and Judge excluded. Parallel phase intervals use their union.", "judge_limitation": "Same-family automated Judge; original-document evidence; human review pending.", "judge_configuration": {"reasoning_effort": "none", "evidence_format": "original source paragraph references"}, "changes": changes, "stop_on_error": True}
    git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, capture_output=True, text=True, check=False)
    manifest["git_head"] = git.stdout.strip() if git.returncode == 0 else None
    continue_workflow = bool(workflow_stage or rag_stage) or category in {"workflow", "rag"} and bool(case_ids)
    manifest["rag_stage"] = rag_stage
    if category == "rag":
        from backend.app.customer_document_ingestion import discover_product_documents
        from evals.dataset import validate_rag_cases
        manifest["source_hashes"] = {path.relative_to(PROJECT_ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in discover_product_documents()}
        manifest["rag_dataset_validation"] = validate_rag_cases([case.model_dump() for case in cases if case.category == "rag"])
        manifest["scope"] = "Real API ingress and PostgreSQL; Answer Pipeline and audited scoring frozen during each run. Development optimization only."
        manifest["judge_configuration"] = {"reasoning_effort": "low", "evidence_format": "separate answer-only literal coverage and original-source grounding", "scoring": "frozen question-scoped core facts; optional coverage reported separately"}
        manifest["report_path"] = destination.relative_to(PROJECT_ROOT).as_posix() if destination.is_relative_to(PROJECT_ROOT) else str(destination)
        manifest["rubric_sha256"] = hashlib.sha256((PROJECT_ROOT / "evals/optimization/answer-rubric.json").read_bytes()).hexdigest() if (PROJECT_ROOT / "evals/optimization/answer-rubric.json").exists() else None
    manifest.update(workflow_stage=workflow_stage, stop_on_error=not continue_workflow, workflow_scoring="frozen_workflow_semantic_contract_v1")
    if category == "workflow":
        manifest.update(scope="Real API ingress, simulator HTTP, PostgreSQL and worker; Agent and scoring code frozen during each run.", judge_limitation="Same-family semantic Judge plus independent backend readback; automated judgments remain fallible.", judge_configuration={"primary_reasoning_effort": "none", "mandatory_fallback_reasoning_effort": "low", "evidence_format": "frozen semantic contract, actual tools, persisted actions/tickets and backend facts"})
    from evals.artifacts import history_entry, index_report, save_results
    from evals.budget import preflight, refresh_prices

    previous_run_id = os.environ.get("EVAL_RUN_ID")
    os.environ["EVAL_RUN_ID"] = run_id
    results = []
    processes = []
    original_database = os.environ.get("DATABASE_URL")
    setup_error = None
    deadline = time.monotonic() + 25 * 60 if mode == "quick" and not workflow_stage and not rag_stage else None
    try:
        try:
            pricing = refresh_prices()
            manifest["pricing"] = {name: pricing.get(name) for name in ("model", "routing_tag", "prompt", "completion", "checked_at_utc")}
            manifest["budget_preflight"] = preflight([case for case, _, _ in runs])
            manifest["environment"] = prepare_database(directory, any(case.category == "rag" for case in selected), reuse=mode == "quick")
            processes = start_services(directory)
        except Exception as error:  # noqa: BLE001 - infrastructure failure is recorded, never silently skipped
            from evals.execute import safe_error

            setup_error = type(error).__name__ + ": " + safe_error(error)
            manifest["setup_error"] = setup_error
        for number, (case, variant, iteration) in enumerate(runs, 1):
            if setup_error:
                result = empty_result(case, variant, iteration)
                result["failure_reasons"] = [setup_error]
            elif deadline is not None and time.monotonic() >= deadline:
                result = empty_result(case, variant, iteration)
                result.update(status="timeout", failure_reasons=["Quick Evaluation reached the 25-minute limit"])
            else:
                case_timeout = min(timeout, max(0.1, deadline - time.monotonic())) if deadline is not None else timeout
                result = execute_case(case, variant, iteration, number, directory, case_timeout)
            results.append(result)
            if case["category"] == "workflow":
                result["failure_categories"] = workflow_failure_categories(result)
            if case["category"] == "rag":
                from evals.rag import failure_categories
                result["failure_categories"] = failure_categories(result)
            write_json(directory / "evaluation_results.json", index_report(manifest, results))
            category_finished = number == len(runs) or runs[number][0]["category"] != case["category"]
            if workflow_stage or rag_stage or category_finished or result["status"] in {"error", "timeout"}:
                print(json.dumps({"event": "error" if result["status"] in {"error", "timeout"} else "category_finished", "run": number, "of": len(runs), "category": case["category"], "case_id": case["case_id"], "status": result["status"]}, ensure_ascii=False), flush=True)
            if result["status"] in {"error", "timeout"} and (not continue_workflow or setup_error):
                manifest["stopped_after_error"] = {"case_id": case["case_id"], "variant": variant, "status": result["status"]}
                break
        manifest["finished_at_utc"] = datetime.now(UTC).isoformat()
        manifest["code_unchanged_during_run"] = manifest["code_hashes"] == code_hashes()
        summary = summary_with_comparisons(results)
        manifest["mock_used"] = bool(summary["mock_runs"])
        manifest["baseline_eligible"] = (mode == "final" or bool(workflow_stage or rag_stage)) and len(results) == len(runs) and not manifest.get("stopped_after_error") and manifest["code_unchanged_during_run"] and not summary["mock_runs"]
        if category == "rag":
            from evals.rag import add_summary
            add_summary(summary, results)
        summary.update(planned_runs=len(runs), not_executed_runs=len(runs) - len(results), baseline_eligible=manifest["baseline_eligible"], not_executed_cases=[{"case_id": case["case_id"], "variant": variant, "repeat": iteration} for case, variant, iteration in runs[len(results):]], models=model_names, manifest=manifest)
        # Only replace our own generated report, never an arbitrary output directory.
        if (destination / "evaluation_results.json").exists():
            existing = json.loads((destination / "evaluation_results.json").read_text(encoding="utf-8"))
            if "run_id" not in existing or not (destination / "summary.json").exists():
                raise FileExistsError("Output contains files other than a generated Evaluation report")
            if (destination / "failures").exists():
                failure_directory = (destination / "failures").resolve()
                if not failure_directory.is_relative_to(destination):
                    raise PermissionError("Failure cleanup leaves the selected report directory")
                shutil.rmtree(failure_directory)
        save_results(destination, manifest, results, directory)
        write_json(destination / "summary.json", summary)
        (destination / "summary.md").write_text(render_summary(summary, manifest), encoding="utf-8")
        if rag_stage:
            from evals.rag import history_entry as rag_history_entry
            with (PROJECT_ROOT / "evals" / "BENCHMARK_HISTORY.md").open("a", encoding="utf-8") as handle:
                handle.write(rag_history_entry(summary, manifest))
        stop_services(processes)
        processes = []
        if not directory.resolve().is_relative_to((PROJECT_ROOT / ".local" / "eval" / "runs").resolve()):
            raise PermissionError("Temporary run cleanup leaves the local evaluation directory")
        try:
            shutil.rmtree(directory)
        except OSError as error:
            warning = {"directory": str(directory), "error_type": type(error).__name__, "reason": str(error)}
            manifest["cleanup_warning"] = warning
            write_json(destination / "summary.json", summary)
            (destination / "summary.md").write_text(render_summary(summary, manifest) + "\nTemporary cleanup warning: " + warning["reason"] + "\n", encoding="utf-8")
            print(json.dumps({"event": "cleanup_warning", **warning}, ensure_ascii=False), flush=True)
        if mode == "final":
            archive = PROJECT_ROOT / "reports" / "archive" / run_id
            shutil.copytree(destination, archive)
            issues = "Human ground-truth/Judge review pending." + (" Incomplete or invalid run; do not use for resume data." if not manifest["baseline_eligible"] else "")
            history = PROJECT_ROOT / "evals" / "BENCHMARK_HISTORY.md"
            with history.open("a", encoding="utf-8") as handle:
                handle.write(history_entry(manifest, summary, "../reports/archive/" + run_id + "/summary.md", changes or "Not specified", issues))
        report = {"manifest": manifest, "results": results, "summary": summary}
        print(json.dumps({"output": str(destination / "evaluation_results.json"), "mode": mode, "total_runs": len(results), "passed": summary["passed"], "failed": summary["failed"], "error": summary["error"], "timeout": summary["timeout"]}), flush=True)
        return report
    finally:
        stop_services(processes)
        for name, value in (("DATABASE_URL", original_database), ("EVAL_RUN_ID", previous_run_id)):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        get_settings.cache_clear()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python -m evals.run", description="Quick module feedback by default; Final Benchmark only when explicitly selected.")
    parser.add_argument("--mode", choices=["quick", "final"], default="quick")
    parser.add_argument("--suite", type=Path, default=PROJECT_ROOT / "evals" / "smoke.jsonl")
    parser.add_argument("--category", choices=CATEGORIES)
    parser.add_argument("--cases", nargs="+", help="Quick only: explicit case IDs from one category; up to 11 Workflow cases, otherwise 5")
    parser.add_argument("--retrieval-modes", nargs="+", choices=MODES)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--changes", default="", help="Final only: describe this benchmark's changes in BENCHMARK_HISTORY.md")
    parser.add_argument("--validate", action="store_true", help="Validate dataset paths and labels without execution or external calls")
    parser.add_argument("--workflow-stage", choices=["baseline", "optimized", "holdout"], help="Frozen Workflow split only; Holdout runs once after optimized Development")
    parser.add_argument("--rag-stage", choices=["baseline", "optimized", "holdout"], help="Frozen RAG split only; Holdout runs once after optimized Development")
    args = parser.parse_args()
    if args.validate:
        validation = validate_smoke(load_cases(args.suite))
        print(json.dumps(validation, ensure_ascii=False, indent=2))
        if not validation["valid"]:
            raise SystemExit(1)
        return
    try:
        select_cases(load_cases(args.suite), args.mode, args.category, args.cases, args.retrieval_modes, args.workflow_stage, args.rag_stage)
    except ValueError as error:
        parser.error(str(error))
    report = run_evaluation(args.suite, args.mode, args.category, args.cases, args.retrieval_modes, args.timeout, changes=args.changes, workflow_stage=args.workflow_stage, rag_stage=args.rag_stage)
    if report["summary"]["failed"] or report["summary"]["error"] or report["summary"]["timeout"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
