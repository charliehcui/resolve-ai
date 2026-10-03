"""Explicit denominators, missing evidence and wall-clock statistics."""
import re
from collections import Counter

SUCCESS_METRICS = ("task_success_rate", "recall_at_5", "mrr", "answer_accuracy_before", "answer_accuracy_after", "tool_selection_accuracy", "tool_argument_accuracy", "diagnosis_accuracy", "handoff_accuracy", "unauthorized_action_blocking_rate", "invalid_action_rejection_rate", "valid_action_completion_rate", "recovery_success_rate")
RATIO_METRICS = ("unsupported_claim_rate_before", "unsupported_claim_rate_after", "supported_claim_retention_rate")
ADVERSE_METRICS = ("duplicate_business_effect_rate", "false_success_rate")
METRIC_NAMES = SUCCESS_METRICS + RATIO_METRICS + ADVERSE_METRICS


def applicable_metrics(case: dict) -> list[str]:
    category = case["category"]
    if category == "rag":
        return ["recall_at_5", "mrr", "answer_accuracy_before", "answer_accuracy_after", *RATIO_METRICS, "handoff_accuracy"]
    if category == "workflow":
        names = ["tool_selection_accuracy", "tool_argument_accuracy", "diagnosis_accuracy", "handoff_accuracy"]
        return names
    kind = case["expected"]["kind"]
    metric = {"unauthorized": "unauthorized_action_blocking_rate", "invalid": "invalid_action_rejection_rate", "valid": "valid_action_completion_rate", "recovery": "recovery_success_rate"}[kind]
    return [metric, *ADVERSE_METRICS]


def retrieval_scores(ranked: list[str], relevant: list[str]) -> dict:
    truth = set(relevant)
    if not truth:
        raise ValueError("Retrieval ground truth must contain at least one relevant chunk")
    recall = len(set(ranked[:5]) & truth) / len(truth)
    reciprocal = next((1 / rank for rank, chunk in enumerate(ranked[:5], 1) if chunk in truth), 0.0)
    return {"recall_at_5": recall, "mrr": reciprocal}


def tool_scores(selected: list[dict], expected: dict, identifiers: dict) -> dict:
    names = {tool.get("name") for tool in selected}
    acceptable = set(expected["acceptable_tools"])
    required_groups = expected.get("required_any", [])
    selection = names.issubset(acceptable) and all(names & set(group) for group in required_groups)
    if not acceptable:
        return {"tool_selection_accuracy": float(not selected), "tool_argument_accuracy": float(not selected)}
    arguments_correct = bool(selected)
    for tool in selected:
        rules = expected.get("arguments", {}).get(tool.get("name"))
        args = tool.get("args")
        if rules is None or not isinstance(args, dict) or set(args) != set(rules):
            arguments_correct = False
            continue
        for name, value in rules.items():
            wanted = identifiers[value[1:]] if isinstance(value, str) and value.startswith("$") else value
            if args.get(name) != wanted:
                arguments_correct = False
    return {"tool_selection_accuracy": float(selection), "tool_argument_accuracy": float(arguments_correct and selection)}


def workflow_action_check(case: dict, output: dict) -> dict:
    plan = output["turn"].get("action_plan") or {}
    action = plan.get("action_type")
    allowed = case["expected"].get("acceptable_actions")
    if allowed is None:
        return {"passed": action == case["expected_action"], "acceptable_actions": [case["expected_action"]], "checks": {"exact_action": action == case["expected_action"]}}
    if action not in allowed or action not in {"retry_order_sync", "retry_failed_task"}:
        return {"passed": False, "acceptable_actions": allowed, "checks": {"acceptable_action": False}}

    initial = output["initial"]
    business = output["after_business"]
    source = business.get("source") or {}
    shop = business.get("shop") or {}
    snapshot = plan.get("source_snapshot") or {}
    candidate = {}
    for decision in reversed(output["observations"]["diagnoses"]):
        candidate = (decision.get("investigation_complete") or {}).get("recommended_action") or {}
        if candidate:
            break
    records = {record["evidence_id"]: record for record in (output.get("snapshot", {}).get("case") or {}).get("evidence", [])}
    plan_ids = plan.get("evidence_ids") or []
    candidate_ids = candidate.get("evidence_ids") or []
    checks = {"plan_proposed": plan.get("status") == "proposed", "plan_scope": plan.get("company_id") == case["permissions"]["company_id"] and plan.get("shop_id") == initial["shop_id"] and plan.get("external_order_id") == initial["order_id"], "paid_source": source.get("payment_status") == "paid", "no_merchant_order": business.get("orders") == [], "shop_ready": shop.get("sync_enabled") is True and shop.get("connection_status") == "authorized", "source_snapshot_matches": bool(source.get("event_id")) and source.get("version") is not None and all(snapshot.get(field) == source.get(field) for field in ("event_id", "version", "sku", "quantity", "amount_minor", "payment_status")), "candidate_matches": candidate.get("action_type") == action, "evidence_complete": bool(plan_ids and candidate_ids) and set(plan_ids + candidate_ids).issubset(records)}
    mapping = None
    for row in business.get("business_rows", {}).get("merchant.sku_mappings", []):
        if row.get("company_id") == plan.get("company_id") and row.get("shop_id") == initial["shop_id"] and row.get("platform_sku") == source.get("sku") and row.get("active") is True:
            mapping = row
    checks["active_mapping"] = bool(mapping and mapping.get("merchant_sku") and snapshot.get("merchant_sku") == mapping["merchant_sku"])

    indexed = {}
    candidate_records = {}
    cited_tools = set()
    checks["evidence_scope"] = True
    for evidence_id in set(plan_ids + candidate_ids):
        record = records.get(evidence_id) or {}
        request = record.get("request") or {}
        scoped = request.get("shop_id") == initial["shop_id"] and ("order_id" not in request or request["order_id"] == initial["order_id"]) and ("platform_sku" not in request or request["platform_sku"] == source.get("sku"))
        usable = scoped and record.get("status") == "success" and record.get("source_service") != "support"
        checks["evidence_scope"] = checks["evidence_scope"] and usable
        if usable and evidence_id in candidate_ids:
            cited_tools.add(record["tool_name"])
            candidate_records[record["tool_name"]] = record["response"]
        if usable and evidence_id in plan_ids:
            indexed[record["tool_name"]] = record["response"]
    required = {"GetOrder", "GetOrderProcessRecords", "GetShopSyncStatus", "GetShopConnectionStatus", "GetSkuMapping"}
    checks["plan_evidence"] = required.issubset(indexed)
    processing = indexed.get("GetOrderProcessRecords") or {}
    checks["failed_processing"] = processing.get("task_status") == "failed" and processing.get("error_code") == "TRANSIENT_PROCESSING_ERROR"
    if action == "retry_order_sync":
        checks["candidate_evidence"] = {"GetOrder", "GetOrderProcessRecords"}.issubset(cited_tools)
    else:
        task = indexed.get("GetWorkerTask") or {}
        checks["candidate_evidence"] = "GetWorkerTask" in cited_tools
        cited_task = candidate_records.get("GetWorkerTask") or {}
        checks["retryable_task"] = bool(task.get("task_id")) and task.get("version") is not None and task.get("status") == "failed" and task.get("retryable") is True and snapshot.get("worker_task") == {field: task.get(field) for field in ("task_id", "version", "status", "attempts")}
        checks["candidate_task_matches"] = cited_task.get("retryable") is True and all(cited_task.get(field) == task.get(field) for field in ("task_id", "event_id", "version", "status")) and task.get("event_id") == source.get("event_id")
    return {"passed": all(checks.values()), "acceptable_actions": allowed, "checks": checks}


def workflow_business_claim_check(case: dict, answer: str) -> dict:
    rules = case["claim_ground_truth"].get("forbidden_promises", [])
    if set(rules) - {"automatic_retry_after_recovery"}:
        raise ValueError("Unknown critical business claim rule")
    violations = []
    if "automatic_retry_after_recovery" in rules:
        pattern = r"自动.{0,12}(?:重试|重新处理|重新同步|再试)|auto(?:matic(?:ally)?)?[-\s]*(?:retry|retries|retried|retrying)|(?:retry|retries|retried|retrying).{0,12}automatically"
        for clause in re.split(r"[。！？；\n.!?;，,]", answer):
            for match in re.finditer(pattern, clause, flags=re.IGNORECASE):
                before = clause[:match.start()]
                after = clause[match.end():]
                negated = re.search(r"(?:不会|不应|不能(?:承诺|保证)?|无法(?:确认|保证)?|尚未确认|没有(?:证据|依据)?|不保证|不承诺|不代表|不意味着|是否).{0,20}$|\b(?:not|never|cannot|can't|no)\b.{0,25}$", before, flags=re.IGNORECASE)
                denied_after = re.match(r"[\s\"'”）)]*(?:尚未实现|未实现|没有(?:证据|依据|实现)|没有实现|缺乏依据|没有代码支持)|\s+(?:is |are )?(?:not implemented|not guaranteed|unsupported)", after, flags=re.IGNORECASE)
                if not negated and not denied_after:
                    violations.append({"rule": "automatic_retry_after_recovery", "text": clause.strip()})
    return {"checked": bool(rules), "rules": rules, "passed": not violations if rules else None, "violations": violations, "limitation": "Targeted critical-claim rules, not a complete semantic fact judge."}


def claim_scores(before: list[dict], after: list[dict], judgment: dict) -> dict:
    checks = judgment["claim_checks"]
    if sorted(item["index"] for item in checks) != list(range(len(before))):
        raise ValueError("Independent judge must assess every pre-validation claim exactly once")
    supported = {item["index"] for item in checks if item["supported"]}
    remaining = Counter(item["text"] for item in after)
    retained = []
    for index, claim in enumerate(before):
        if remaining[claim["text"]] > 0:
            retained.append(index)
            remaining[claim["text"]] -= 1
    if any(remaining.values()):
        raise ValueError("Final claims contain text absent from pre-validation observation")
    return {"answer_accuracy_before": float(judgment["answer_correct_before"]), "answer_accuracy_after": float(judgment["answer_correct_after"]), "unsupported_claim_rate_before": {"numerator": len(before) - len(supported), "denominator": len(before)}, "unsupported_claim_rate_after": {"numerator": sum(index not in supported for index in retained), "denominator": len(after)}, "supported_claim_retention_rate": {"numerator": sum(index in supported for index in retained), "denominator": len(supported)}}


def percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower), 3)


def summarize(results: list[dict]) -> dict:
    metrics = {}
    for name in METRIC_NAMES:
        eligible = [result for result in results if result["category"] == "workflow"] if name == "task_success_rate" else [result for result in results if name in result["applicable_metrics"]]
        measured = [float(result["status"] == "passed") for result in eligible] if name == "task_success_rate" else [result["metrics"][name] for result in eligible if result["metrics"].get(name) is not None]
        missing = len(eligible) - len(measured)
        if name in RATIO_METRICS:
            numerator = sum(item["numerator"] for item in measured)
            denominator = sum(item["denominator"] for item in measured)
            value = numerator / denominator if denominator and not missing else None
        else:
            numerator = sum(measured)
            denominator = len(eligible)
            requires_complete_evidence = name in {"answer_accuracy_before", "answer_accuracy_after", *ADVERSE_METRICS}
            value = numerator / denominator if denominator and (not requires_complete_evidence or not missing) else None
        metrics[name] = {"value": value, "numerator": numerator, "denominator": denominator, "eligible_runs": len(eligible), "measured_runs": len(measured), "missing_evidence_runs": missing}
        if name in {"answer_accuracy_before", "answer_accuracy_after"}:
            metrics[name]["measured_only_value"] = numerator / len(measured) if measured else None
            metrics[name]["accuracy_bounds"] = [numerator / denominator, (numerator + missing) / denominator] if denominator else None
            metrics[name]["scoring_policy"] = "Missing judge evidence keeps the full denominator but makes complete-dataset accuracy unknown; it is not an incorrect answer. Empty answers are deterministically incorrect."
    latency = [result["performance"]["end_to_end_latency_ms"] for result in results if result["performance"].get("end_to_end_latency_ms") is not None]
    performance = {"p50_latency_ms": percentile(latency, 0.5), "p95_latency_ms": percentile(latency, 0.95), "latency_measured_runs": len(latency), "latency_missing_runs": len(results) - len(latency), "latency_censored_runs": sum(bool(result["performance"].get("latency_censored")) for result in results)}
    for name in ("llm_call_count", "tool_call_count", "input_tokens", "output_tokens", "llm_latency_ms", "retrieval_latency_ms", "tool_execution_latency_ms"):
        values = [result["performance"][name] for result in results if result["performance"].get(name) is not None]
        performance[name] = {"mean": sum(values) / len(values) if values else None, "measured_runs": len(values), "missing_runs": len(results) - len(values)}
    tokens = [result["performance"]["input_tokens"] + result["performance"]["output_tokens"] for result in results if result["performance"].get("input_tokens") is not None and result["performance"].get("output_tokens") is not None]
    performance["total_tokens"] = {"mean": sum(tokens) / len(tokens) if tokens else None, "measured_runs": len(tokens), "missing_runs": len(results) - len(tokens)}
    counts = Counter(result["status"] for result in results)
    return {"total_runs": len(results), "unique_cases": len({result["case_id"] for result in results}), "passed": counts["passed"], "failed": counts["failed"], "error": counts["error"], "timeout": counts["timeout"], "not_scored": counts["not_scored"], "metrics": metrics, "performance": performance, "failures": [{"case_id": result["case_id"], "variant": result["variant"], "repeat": result["repeat"], "status": result["status"], "reasons": result["failure_reasons"]} for result in results if result["status"] in {"failed", "error", "timeout"}]}
