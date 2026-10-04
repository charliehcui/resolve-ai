"""Frozen RAG reports and retrieval-only comparison, using the existing search functions."""
import argparse
import json
import os
import time
from collections import Counter
from urllib.parse import urlsplit, urlunsplit

from backend.app.config import PROJECT_ROOT, get_settings, psycopg_url
from evals.metrics import execution_failure_kind, rag_retrieval_scores


def failure_categories(result: dict) -> list[str]:
    if result["status"] in {"error", "timeout"}:
        kind = execution_failure_kind(result)
        if kind == "provider_error":
            return ["Provider Error"]
        if result.get("scoring_status") == "judge_error" or kind == "preflight_error":
            return ["Evaluation Error"]
        return ["Application Error"]
    categories = []
    recall = result["metrics"].get("recall_at_5", 0)
    if recall < 1:
        categories.append("Missing Relevant Source")
        if len(result["case"]["expected"]["source_groups"]) > 1:
            categories.append("Multi-Source Incomplete")
        if result.get("retrieval_evidence"):
            categories.append("Wrong Source")
        else:
            categories.append("Query Understanding / Rewrite")
        if any(run.get("filters", {}).get("product") == result["case"]["permissions"]["company_id"] for run in result.get("retrieval_runs", [])):
            categories.append("Metadata / Scope Problem")
    if 0 < result["metrics"].get("mrr", 0) < 1:
        categories.append("Low Ranking")
    if result["metrics"].get("answer_accuracy_after") == 0:
        if recall == 1:
            categories.append("Correct Retrieval but Wrong Answer")
        if not result["case"]["expected"]["answerable"]:
            categories.append("No-Answer Failure")
    judgment = result.get("independent_judge", {}).get("judgment") or {}
    if judgment.get("unsupported_final_claims") or judgment.get("forbidden_claims") or result["metrics"].get("unsupported_claim_rate_after", {}).get("numerator", 0):
        categories.append("Unsupported Claim")
    if any(run.get("rerank_error") for run in result.get("retrieval_runs", [])):
        categories.append("Reranker Problem")
    return sorted(set(categories))


def add_summary(summary: dict, results: list[dict]) -> None:
    abstentions = [result for result in results if not result["case"]["expected"]["answerable"]]
    summary["rag_checks"] = {"failure_categories": dict(Counter(category for result in results for category in result.get("failure_categories", []))), "no_answer": {"correct": sum(result.get("no_answer_correct") is True for result in abstentions), "total": len(abstentions), "missing_evidence": sum(result.get("no_answer_correct") is None for result in abstentions)}, "unsupported_final_claims": sum(len((result.get("independent_judge", {}).get("judgment") or {}).get("unsupported_final_claims", [])) for result in results), "forbidden_claims": sum(len((result.get("independent_judge", {}).get("judgment") or {}).get("forbidden_claims", [])) for result in results), "provider_error": sum(result.get("failure_categories") == ["Provider Error"] for result in results), "evaluation_error": sum(result.get("failure_categories") == ["Evaluation Error"] for result in results)}
    from evals.judge import rag_rubric

    coverage = {name: {"numerator": 0, "denominator": 0, "missing_evidence_cases": 0} for name in ("core_fact_coverage", "all_fact_coverage", "expected_fact_coverage")}
    for result in results:
        judgment = result.get("independent_judge", {}).get("judgment") or {}
        for name in ("core_fact_coverage", "all_fact_coverage"):
            measured = judgment.get(name)
            if measured:
                coverage[name]["numerator"] += measured["numerator"]
                coverage[name]["denominator"] += measured["denominator"]
            else:
                coverage[name]["missing_evidence_cases"] += 1
                try:
                    rubric = rag_rubric(result["case"], cached_only=True)
                    coverage[name]["denominator"] += sum(part["core"] or name == "all_fact_coverage" for part in rubric["parts"])
                except FileNotFoundError:
                    pass
        facts = judgment.get("fact_checks")
        coverage["expected_fact_coverage"]["denominator"] += len(result["case"]["claim_ground_truth"]["required_facts"])
        if facts is None:
            coverage["expected_fact_coverage"]["missing_evidence_cases"] += 1
        else:
            coverage["expected_fact_coverage"]["numerator"] += sum(fact["passed"] for fact in facts)
    for metric in coverage.values():
        metric["confirmed_fraction"] = metric["numerator"] / metric["denominator"] if metric["denominator"] else None
        metric["value"] = metric["confirmed_fraction"] if not metric["missing_evidence_cases"] else None
    summary["rag_checks"].update(coverage)
    events = [event for result in results for event in [*result.get("observations", {}).get("structured_output", []), *result.get("independent_judge", {}).get("structured_output", [])]]
    summary["rag_checks"]["structured_output_errors"] = sum(event.get("status") == "structured_output_error" for event in events)
    summary["rag_checks"]["structured_output_recoveries"] = sum(event.get("recovery") == "raw_json" or event.get("attempt") == 2 and event.get("status") == "valid" for event in events)
    summary["rag_checks"]["structured_output_by_stage"] = {stage: {"errors": sum(event.get("stage") == stage and event.get("status") == "structured_output_error" for event in events), "recoveries": sum(event.get("stage") == stage and (event.get("recovery") == "raw_json" or event.get("attempt") == 2 and event.get("status") == "valid") for event in events)} for stage in sorted({event.get("stage", "unknown") for event in events})}
    summary["rag_checks"]["upstream_provider_errors"] = sum(row.get("status") == "http_error" for result in results for row in result.get("llm_accounting", []))


def history_entry(summary: dict, manifest: dict) -> str:
    title = {"baseline": "RAG Baseline", "optimized": "RAG Optimized Development", "holdout": "RAG Holdout"}[manifest["rag_stage"]]
    report_path = manifest.get("report_path", "reports/rag/" + manifest["rag_stage"]) + "/summary.md"
    lines = ["## " + title, "", f"- Run: `{manifest['run_id']}`; mode: `{manifest['retrieval_modes'][0]}`; passed: {summary['passed']}/{manifest['selected_cases']}; Error / Timeout: {summary['error']} / {summary['timeout']}.", f"- Report: [{report_path}](../{report_path})", "", "| Metric | Value |", "|---|---:|"]
    for name in ("task_success_rate", "answer_accuracy_after", "recall_at_5", "mrr", "unsupported_claim_rate_after"):
        lines.append(f"| {name} | {summary['metrics'][name]['value']} |")
    performance = summary["performance"]
    cost = summary.get("cost", {}).get("run", {})
    lines += ["", "- Checks: " + json.dumps(summary["rag_checks"], ensure_ascii=False), f"- Latency P50 / P95: {performance['p50_latency_ms']} / {performance['p95_latency_ms']} ms.", f"- Application + Judge known tokens: {cost.get('total_tokens')}; OpenRouter actual / accounted: ${cost.get('actual_usd')} / ${cost.get('accounted_usd')}; missing call usage: {cost.get('usage_missing_calls')}; fallback calls: {cost.get('fallback_count')}.", "- Gemini query embeddings are outside the OpenRouter ledger; existing document embeddings reused.", ""]
    if manifest.get("rubric_sha256"):
        lines.append("- Answer optimization uses the frozen question-scoped scoring audit. Compare with regraded saved Development answers; earlier all-or-nothing scores use a different policy and cannot measure Agent-only gains.")
    elif manifest["rag_stage"] == "optimized":
        baseline = json.loads((PROJECT_ROOT / "reports/rag/baseline/summary.json").read_text(encoding="utf-8"))
        lines.append("- Baseline → Optimized: " + "; ".join(name + ": " + str(baseline["metrics"][name]["value"]) + " → " + str(summary["metrics"][name]["value"]) for name in ("task_success_rate", "answer_accuracy_after", "recall_at_5", "mrr")))
    return "\n".join(lines) + "\n\n"


def render_summary(summary: dict, manifest: dict) -> str:
    lines = ["# RAG " + (manifest.get("rag_stage") or "Targeted Evaluation"), "", f"Run: `{manifest['run_id']}`; mode: `{manifest['retrieval_modes'][0]}`; passed: {summary['passed']}/{manifest['selected_cases']}; Error: {summary['error']}; Timeout: {summary['timeout']}; unexecuted: {summary.get('not_executed_runs', 0)}.", "", "| Metric | Value | Evidence |", "|---|---:|---|"]
    for name in ("task_success_rate", "answer_accuracy_after", "recall_at_5", "mrr", "unsupported_claim_rate_after"):
        metric = summary["metrics"][name]
        lines.append(f"| {name} | {metric['value']} | {metric['numerator']}/{metric['denominator']}; missing evidence runs: {metric['missing_evidence_runs']} |")
    checks = summary["rag_checks"]
    performance = summary["performance"]
    cost = summary.get("cost", {}).get("run", {})
    lines += ["", "- No-answer: " + json.dumps(checks["no_answer"]), f"- Unsupported final claims: {checks['unsupported_final_claims']}; forbidden claims: {checks['forbidden_claims']}.", f"- Provider Error: {checks['provider_error']}; Evaluation Error: {checks['evaluation_error']}; runtime errors: {summary['error']}.", f"- Application latency P50 / P95: {performance['p50_latency_ms']} / {performance['p95_latency_ms']} ms; setup and Judge excluded.", f"- Application tokens per case (mean): {performance['total_tokens']['mean']}; application + Judge known tokens: {cost.get('total_tokens')}; missing call usage: {cost.get('usage_missing_calls')}.", f"- OpenRouter actual / accounted cost: ${cost.get('actual_usd')} / ${cost.get('accounted_usd')}; fallback calls: {cost.get('fallback_count')}.", "- Gemini query embedding charges are outside this ledger. Existing document embeddings reused; no knowledge files/labels changed.", "- Accuracy is unknown when Judge/provider evidence is missing; measured-only score and bounds remain in summary.json. Retrieval uses the actual final top five, source-group AND/OR and topic evidence. Claim rate counts final claims and uncited fallback assertions.", "- Same-family automated Judge; semantic errors remain possible.", "", "Failure categories: " + json.dumps(checks["failure_categories"], ensure_ascii=False), "", "Failed / Error cases:"]
    lines += ["", "- Core fact coverage: " + json.dumps(checks.get("core_fact_coverage")), "- All atomic fact coverage: " + json.dumps(checks.get("all_fact_coverage")), "- Original compound expected-fact coverage: " + json.dumps(checks.get("expected_fact_coverage")), "- Final Task Success checks frozen question-required parts and all unsupported/forbidden assertions; optional completeness is recorded separately."]
    for failure in summary["failures"]:
        lines.append(f"- {failure['case_id']}: {failure['status']}: {'; '.join(failure['reasons'])}")
    if manifest.get("rag_stage") == "optimized" and not manifest.get("rubric_sha256"):
        baseline = json.loads((PROJECT_ROOT / "reports/rag/baseline/summary.json").read_text(encoding="utf-8"))
        lines += ["", "Baseline → Optimized:"]
        for name in ("task_success_rate", "answer_accuracy_after", "recall_at_5", "mrr", "unsupported_claim_rate_after"):
            lines.append(f"- {name}: {baseline['metrics'][name]['value']} → {summary['metrics'][name]['value']}")
    return "\n".join(lines) + "\n"


def retrieval_comparison() -> dict:
    from backend.app.customer_retrieval import fetch_visible_chunks, keyword_search, reciprocal_rank_fusion, rerank_chunks, vector_search
    from backend.app.models import UserContext
    from evals.dataset import load_cases
    from backend.app.customer_document_ingestion import generate_text_embeddings
    from backend.app.database import get_connection
    from backend.app.models import RetrievedChunk

    cases = [case.model_dump() for case in load_cases(PROJECT_ROOT / "evals/data/smoke.jsonl") if case.category == "rag" and case.expected["split"] == "development"]
    baseline = json.loads((PROJECT_ROOT / "reports/rag/baseline/evaluation_results.json").read_text(encoding="utf-8"))
    baseline_rows = {row["case_id"]: row for row in baseline["results"]}
    cache = json.loads((PROJECT_ROOT / ".local/eval/quick/environment.json").read_text(encoding="utf-8"))
    if not cache["database"].startswith("resolveai_eval_"):
        raise PermissionError("Comparison requires an isolated evaluation database")
    parts = urlsplit(psycopg_url(get_settings().database_url))
    os.environ["DATABASE_URL"] = urlunsplit((parts.scheme, parts.netloc, "/" + cache["database"], parts.query, parts.fragment))
    os.environ["LANGSMITH_TRACING"] = "false"
    get_settings.cache_clear()
    user = UserContext(company_id="company-a", user_id="staff-a", role="staff")
    all_chunks = fetch_visible_chunks(user)
    by_id = {chunk.chunk_id: chunk for chunk in all_chunks}
    report = {"scope": "Development only. Identical original question and authenticated fixture scope across all three modes; scope repaired for this controlled retrieval comparison, independently of unchanged Baseline Agent results. Query embeddings batched once and reused by all modes; no answers or LLM judge calls.", "cases": [], "modes": {}}
    cache_path = PROJECT_ROOT / ".local/eval/rag-query-vectors.json"
    queries = [case["question"] for case in cases]
    cached_vectors = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else None
    if cached_vectors and cached_vectors["queries"] == queries:
        vectors_by_query = cached_vectors["vectors"]
    else:
        vectors_by_query = generate_text_embeddings(queries, "RETRIEVAL_QUERY")
        cache_path.write_text(json.dumps({"queries": queries, "vectors": vectors_by_query}), encoding="utf-8")
    for case, query_vector in zip(cases, vectors_by_query, strict=True):
        started = time.perf_counter()
        baseline_runs = baseline_rows[case["case_id"]].get("retrieval_runs", [])
        run = baseline_runs[-1] if baseline_runs else None
        query = case["question"]
        filters = {"product": case["initial_state"]["product"], "version": case["initial_state"]["version"]}
        visible = [chunk for chunk in all_chunks if (not filters.get("version") or chunk.version == filters["version"]) and (not filters.get("product") or f"产品：{filters['product']}" in chunk.content)]
        from backend.app.customer_retrieval import filter_sql
        where, parameters = filter_sql(user, filters["product"], filters["version"])
        parameters["query_vector"] = json.dumps(query_vector)
        with get_connection() as connection:
            rows = connection.execute(f"SELECT c.chunk_id::text, d.title, d.source_uri, d.version, c.content, 1 - (c.embedding <=> %(query_vector)s::vector) AS score FROM support.document_chunks c JOIN support.product_documents d ON d.document_id=c.document_id WHERE {where} ORDER BY c.embedding <=> %(query_vector)s::vector LIMIT 15", parameters).fetchall()
        vectors = [RetrievedChunk(**row) for row in rows]
        keywords = keyword_search(query, visible)
        fused = reciprocal_rank_fusion([vectors, keywords])
        rerank_error = None
        try:
            reranked = rerank_chunks(query, fused)
        except Exception as error:
            rerank_error = type(error).__name__
            reranked = fused[:5]
        modes = {"vector_only": vectors[:5], "hybrid": fused[:5], "hybrid_rerank": reranked[:5]}
        row = {"case_id": case["case_id"], "query": query, "filters": filters, "embedding_reused_across_modes": True, "rerank_error": rerank_error, "elapsed_ms": round((time.perf_counter() - started) * 1000), "modes": {mode: rag_retrieval_scores([chunk.model_dump() for chunk in chunks], case) for mode, chunks in modes.items()}}
        report["cases"].append(row)
        print(json.dumps({"case_id": case["case_id"], "modes": {mode: {key: scores[key] for key in ("recall_at_5", "mrr")} for mode, scores in row["modes"].items()}, "rerank_error": rerank_error}), flush=True)
        (PROJECT_ROOT / ".local/eval/rag-retrieval-comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for mode in ("vector_only", "hybrid", "hybrid_rerank"):
        report["modes"][mode] = {key: sum(row["modes"][mode][key] for row in report["cases"]) / len(cases) for key in ("recall_at_5", "mrr")}
    (PROJECT_ROOT / "reports/latest").mkdir(exist_ok=True)
    (PROJECT_ROOT / "reports/latest/retrieval_comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (PROJECT_ROOT / ".local/eval/rag-retrieval-comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Retrieval-only comparison of the frozen 30 RAG Development cases")
    parser.add_argument("--retrieval-only", action="store_true", required=True)
    parser.parse_args()
    print(json.dumps(retrieval_comparison()["modes"], indent=2))
