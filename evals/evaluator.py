"""Evaluation scoring, metric aggregation and compact result recording."""
import json
import re
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SUCCESS_METRICS = ("task_success_rate", "recall_at_5", "mrr", "answer_accuracy_before", "answer_accuracy_after", "tool_selection_accuracy", "tool_argument_accuracy", "diagnosis_accuracy", "handoff_accuracy", "unauthorized_action_blocking_rate", "invalid_action_rejection_rate", "valid_action_completion_rate", "recovery_success_rate", "idempotency_success_rate")
RATIO_METRICS = ("unsupported_claim_rate_before", "unsupported_claim_rate_after", "supported_claim_retention_rate")
ADVERSE_METRICS = ("duplicate_business_effect_rate", "false_success_rate")
METRIC_NAMES = SUCCESS_METRICS + RATIO_METRICS + ADVERSE_METRICS


def applicable_metrics(case: dict) -> list[str]:
    category = case["category"]
    if category == "rag":
        return ["recall_at_5", "mrr", "answer_accuracy_before", "answer_accuracy_after", "unsupported_claim_rate_before", "unsupported_claim_rate_after"]
    if category == "workflow":
        names = ["tool_selection_accuracy", "tool_argument_accuracy", "diagnosis_accuracy", "handoff_accuracy"]
        return names
    kind = case["expected"]["kind"]
    metric = {"unauthorized": "unauthorized_action_blocking_rate", "invalid": "invalid_action_rejection_rate", "valid": "valid_action_completion_rate", "recovery": "recovery_success_rate"}[kind]
    return [metric, *ADVERSE_METRICS, *(['idempotency_success_rate'] if category == 'reliability' else [])]


def retrieval_scores(ranked: list[str], relevant: list[str]) -> dict:
    truth = set(relevant)
    if not truth:
        raise ValueError("Retrieval ground truth must contain at least one relevant chunk")
    recall = len(set(ranked[:5]) & truth) / len(truth)
    reciprocal = next((1 / rank for rank, chunk in enumerate(ranked[:5], 1) if chunk in truth), 0.0)
    return {"recall_at_5": recall, "mrr": reciprocal}


def rag_retrieval_scores(chunks: list[dict], case: dict) -> dict:
    """Top five ranks first, then source-group coverage with topic evidence anchors."""
    groups = case["expected"]["source_groups"]
    anchors = case["claim_ground_truth"]["source_evidence"]
    covered = set()
    first_rank = None
    evidence = []
    for rank, chunk in enumerate(chunks[:5], 1):
        source = chunk["source_uri"]
        content = re.sub(r"\s+", "", chunk["content"])
        topic_match = any(re.sub(r"\s+", "", quote) in content for quote in anchors.get(source, []))
        # 文本来源允许同主题的转述/相邻段落；表格仍须命中指定状态或错误码行。
        if not topic_match and any(source in group for group in groups) and not source.endswith(".xlsx"):
            from backend.app.customer_retrieval import tokenize
            topic = case["expected"].get("expected_topic", "")
            terms = {term for term in tokenize(topic) if len(term) > 1 and any(char.isalnum() for char in term) and term not in {"当前", "资料", "范围", "区别", "边界", "实际", "事实", "条件", "说明", "完整"}}
            body = chunk["content"].split("\n\n", 1)[-1]
            hits = {term for term in terms if term in body.lower()}
            topic_match = len(hits) >= 2 and len(hits) >= len(terms) / 2
        hits = [index for index, group in enumerate(groups) if source in group and topic_match]
        covered.update(hits)
        if hits and first_rank is None:
            first_rank = rank
        evidence.append({"rank": rank, "chunk_id": chunk["chunk_id"], "source_uri": source, "topic_match": topic_match, "source_groups": hits})
    return {"recall_at_5": len(covered) / len(groups), "mrr": 1 / first_rank if first_rank else 0.0, "retrieval_evidence": evidence, "covered_source_groups": sorted(covered)}


def tool_scores(selected: list[dict], expected: dict, identifiers: dict) -> dict:
    names = {tool.get("name") for tool in selected}
    acceptable = set(expected["acceptable_tools"])
    required_groups = expected.get("required_any", [])
    routes = expected.get("required_routes")
    route_complete = any(set(route).issubset(names) for route in routes) if routes is not None else all(names & set(group) for group in required_groups)
    selection = names.issubset(acceptable) and route_complete
    if not acceptable:
        return {"tool_selection_accuracy": float(not selected), "tool_argument_accuracy": float(not selected)}
    arguments_correct = bool(selected)
    for tool in selected:
        rules = expected.get("arguments", {}).get(tool.get("name"))
        if rules is None:
            from backend.app.support_tools import READ_TOOL_SCHEMAS

            schema = next((schema for schema in READ_TOOL_SCHEMAS if schema.__name__ == tool.get("name")), None)
            if schema is not None:
                rules = {field: "$" + field for field in schema.model_fields}
        args = tool.get("args")
        if rules is None or not isinstance(args, dict) or set(args) != set(rules):
            arguments_correct = False
            continue
        for name, value in rules.items():
            wanted = identifiers.get(value[1:]) if isinstance(value, str) and value.startswith("$") else value
            if args.get(name) != wanted:
                arguments_correct = False
    return {"tool_selection_accuracy": float(selection), "tool_argument_accuracy": float(arguments_correct)}


def workflow_failure_categories(result: dict) -> list[str]:
    categories = set()
    kind = execution_failure_kind(result)
    if kind:
        return ["Provider Error" if kind == "provider_error" else "Evaluation Error"]
    case = result.get("case", {})
    expected = case.get("expected_tools", {})
    selected = result.get("observations", {}).get("selected_tools", [])
    names = {item.get("name") for item in selected}
    if names - set(expected.get("acceptable_tools", [])):
        categories.update(("Wrong Tool", "Unnecessary Tool"))
    routes = case.get("workflow_ground_truth", {}).get("required_tool_routes")
    if routes and not any(set(route).issubset(names) for route in routes):
        categories.update(("Missing Tool", "Premature Stop"))
    successful_reads = {(record.get("tool_name"), json.dumps(record.get("request"), sort_keys=True)) for record in (result.get("snapshot", {}).get("case") or {}).get("evidence", []) if record.get("status") in {"success", "empty", "not_found"}}
    seen = set()
    for item in selected:
        key = (item.get("name"), json.dumps(item.get("args"), sort_keys=True))
        if key in seen and key in successful_reads:
            categories.add("Repeated Tool")
        seen.add(key)
    metrics = result.get("metrics", {})
    for name, category in (("tool_argument_accuracy", "Wrong Argument"), ("diagnosis_accuracy", "Wrong Diagnosis"), ("handoff_accuracy", "Wrong Handoff")):
        if metrics.get(name) == 0 and (name != "tool_argument_accuracy" or selected):
            categories.add(category)
    for reason in result.get("failure_reasons", []):
        for category in ("Unnecessary Tool", "Invalid Action", "Unsupported Claim", "Incomplete Action"):
            if reason.startswith(category + ":"):
                categories.add(category)
        if "no persisted" in reason or "no matching" in reason:
            categories.add("Incomplete Action")
        if "unexpected business effect" in reason:
            categories.add("Invalid Action")
    return sorted(categories)


def allowed_workflow_read_retries(output: dict) -> set[int]:
    """The frozen contract permits one scoped retry following an unavailable read."""
    records = (output.get("snapshot", {}).get("case") or {}).get("evidence", [])
    indexed = {}
    for record in records:
        if record.get("source_service") != "support":
            key = (record.get("tool_name"), json.dumps(record.get("request"), sort_keys=True))
            indexed.setdefault(key, record.get("status"))
    counts = Counter()
    permitted = set()
    for index, tool in enumerate(output.get("observations", {}).get("selected_tools", [])):
        key = (tool.get("name"), json.dumps(tool.get("args"), sort_keys=True))
        counts[key] += 1
        if counts[key] == 2 and indexed.get(key) in {"unavailable", "error"}:
            permitted.add(index)
    return permitted


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


def workflow_business_claim_check(case: dict, answer: str, output: dict | None = None) -> dict:
    rules = case["claim_ground_truth"].get("forbidden_promises", [])
    if set(rules) - {"automatic_retry_after_recovery"}:
        raise ValueError("Unknown critical business claim rule")
    violations = []
    if "automatic_retry_after_recovery" in rules:
        pattern = r"自动.{0,12}(?:重试|重新处理|重新同步|再试|处理|导入)|auto(?:matic(?:ally)?)?[-\s]*(?:retry|retries|retried|retrying|process|processing|import)|(?:retry|retries|retried|retrying|processed|imported).{0,12}automatically"
        for clause in re.split(r"[。！？；\n.!?;，,]", answer):
            for match in re.finditer(pattern, clause, flags=re.IGNORECASE):
                before = clause[:match.start()]
                after = clause[match.end():]
                negated = re.search(r"(?:不会|不应|不能(?:承诺|保证)?|无法(?:确认|保证)?|尚未确认|没有(?:证据|依据)?|不保证|不承诺|不代表|不意味着|是否).{0,20}$|\b(?:not|never|cannot|can't|no)\b.{0,25}$", before, flags=re.IGNORECASE)
                denied_after = re.match(r"[\s\"'”）)]*(?:尚未实现|未实现|没有(?:证据|依据|实现)|没有实现|缺乏依据|没有代码支持)|\s+(?:is |are )?(?:not implemented|not guaranteed|unsupported)", after, flags=re.IGNORECASE)
                if not negated and not denied_after:
                    violations.append({"rule": "automatic_retry_after_recovery", "text": clause.strip()})
    # 独立比较真实后台的原因字段，不以 Agent 的结论或引用存在作为正确答案。
    direct_causes = []
    records = ((output or {}).get("snapshot", {}).get("case") or {}).get("evidence", [])
    for record in records:
        if record.get("status") != "success" or record.get("source_service") == "support":
            continue
        pending = [record.get("response", {})]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {"cause", "root_cause", "failure_reason", "reason", "error_message"} and isinstance(item, str):
                        direct_causes.append({"text": item, "evidence_id": record.get("evidence_id")})
                    elif isinstance(item, (dict, list)):
                        pending.append(item)
            elif isinstance(value, list):
                pending.extend(value)
    pattern = r"网络(?:连接)?(?:故障|异常|问题)|(?:平台|系统)(?:正在)?(?:维护|内部故障|故障)|(?:商品|产品)(?:尚)?未创建|(?:映射)?配置(?:尚)?未完成|资源(?:问题|不足|耗尽)|network (?:fault|failure|issue|problem)|platform maintenance|system (?:fault|failure)|resource (?:problem|shortage|exhaustion)|(?:product|item) (?:not|never) created|configuration (?:incomplete|not completed)"
    cause_checks = []
    for clause in re.split(r"[。！？；\n.!?;，,]", answer):
        for match in re.finditer(pattern, clause, flags=re.IGNORECASE):
            if re.search(r"(?:不是|并非|不能认定|没有证据(?:证明|表明))\s*$|\b(?:not|no evidence of)\s*$", clause[:match.start()], flags=re.IGNORECASE):
                continue
            sources = [cause for cause in direct_causes if match.group().casefold() in cause["text"].casefold()]
            cause_checks.append({"text": match.group(), "supported": bool(sources), "sources": sources})
            if not sources:
                violations.append({"rule": "unsupported_specific_cause", "text": clause.strip()})
        if re.search(r"可能(?:由|是|因为|由于)|疑似|猜测|\b(?:might be|could be|possibly)\b", clause, flags=re.IGNORECASE):
            violation = {"rule": "unsupported_specific_cause", "text": clause.strip()}
            if violation not in violations:
                violations.append(violation)
        causal = re.search(r"(?:原因|根因)(?:是|为)|由于|因为|导致|造成|引起|未发布到|\b(?:because|due to|caused by|reason is)\b", clause, flags=re.IGNORECASE)
        if causal:
            cause = clause[causal.end():].strip(" 。.;；")
            sources = [record for record in direct_causes if record["text"] in {clause.strip(" 。.;；"), cause}]
            if not sources:
                violations.append({"rule": "unsupported_causal_explanation", "text": clause.strip()})
    return {"checked": True, "rules": [*rules, "unsupported_specific_cause", "unsupported_causal_explanation"], "passed": not violations, "violations": violations, "specific_cause_checks": cause_checks, "limitation": "Lightweight explicit-causality checks against independent backend fields; implicit causal or factual claims still require evidence review."}


def workflow_result_check(output: dict) -> dict:
    turn = output["turn"]
    snapshot = output.get("snapshot", {})
    ticket = snapshot.get("ticket") or {}
    plan = turn.get("action_plan") or {}
    actions = snapshot.get("actions", [])
    status = turn.get("status")
    violations = []
    ticket_exists = bool(turn.get("ticket_id") and ticket.get("ticket_id") == turn["ticket_id"])
    plan_exists = bool(turn.get("action_plan_id") and plan.get("action_id") == turn["action_plan_id"] and any(action.get("action_id") == turn["action_plan_id"] for action in actions))
    if status == "pending_human" and not ticket_exists:
        violations.append("pending_human has no persisted matching ticket")
    if status == "awaiting_confirmation" and (not plan_exists or plan.get("status") != "proposed"):
        violations.append("awaiting_confirmation has no persisted proposed action")
    for clause in re.split(r"[。；;.!?\n]", turn.get("answer", "")):
        if re.search(r"无需|不需要|不要|不必|\b(?:not|no need|do not)\b", clause, flags=re.IGNORECASE):
            continue
        if re.search(r"(?:需要|建议|请求|交给|转交|转至|转).{0,8}(?:人工|工程师)|\b(?:requires? human|needs? human|escalate to human)\b", clause, flags=re.IGNORECASE) and not ticket_exists and not turn.get("needs_support"):
            violations.append("Answer requests human handling but no matching ticket was created")
        if re.search(r"(?:建议|推荐)(?:执行|重试|恢复)|\b(?:recommend executing|recommend retrying)\b", clause, flags=re.IGNORECASE) and not plan_exists and status != "user_action_required":
            violations.append("Answer recommends an action but no matching plan was created")
    return {"passed": not violations, "ticket_persisted": ticket_exists, "plan_persisted": plan_exists, "violations": violations}


def execution_failure_kind(result: dict) -> str | None:
    if result["status"] not in {"error", "timeout"}:
        return None
    text = " ".join(result.get("failure_reasons", [])).casefold()
    if "primary endpoint" in text or "budgetexceeded" in text:
        return "preflight_error"
    if "judge" in text and ("length limit" in text or "lengthfinishreasonerror" in text):
        return "judge_error"
    if any(value in text for value in ("429", "openai", "provider returned error", "upstream", "rate limit")):
        return "provider_error"
    if "structuredoutputerror" in text or "jsondecodeerror" in text:
        return "model_output_error"
    if result["status"] == "timeout":
        return "timeout"
    if "judge" in text:
        return "judge_error"
    return "evaluation_or_application_error"


def claim_scores(before: list[dict], after: list[dict], judgment: dict, final_answer: str | None = None) -> dict:
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
    scores = {"answer_accuracy_before": float(judgment["answer_correct_before"]), "answer_accuracy_after": float(judgment["answer_correct_after"]), "unsupported_claim_rate_before": {"numerator": len(before) - len(supported), "denominator": len(before)}, "unsupported_claim_rate_after": {"numerator": sum(index not in supported for index in retained), "denominator": len(after)}, "supported_claim_retention_rate": {"numerator": sum(index in supported for index in retained), "denominator": len(supported)}}
    if final_answer is not None and "unsupported_final_claims" in judgment:
        from evals.judge import has_answer_content
        unsupported_texts = {before[index]["text"] for index in retained if index not in supported} | set(judgment["unsupported_final_claims"])
        numerator = sum(claim["text"] in unsupported_texts for claim in after)
        denominator = len(after)
        if not after and has_answer_content(final_answer):
            numerator, denominator = int(bool(unsupported_texts)), 1
        scores["unsupported_claim_rate_after"] = {"numerator": numerator, "denominator": denominator}
    return scores


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
        eligible = [result for result in results if result["category"] in {"workflow", "rag", "safety", "reliability"}] if name == "task_success_rate" else [result for result in results if name in result["applicable_metrics"]]
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
    workflow_cases = {}
    for result in results:
        if result["category"] != "workflow":
            continue
        workflow_cases.setdefault(result["case_id"], None)
        if result["status"] in {"passed", "failed"}:
            workflow_cases[result["case_id"]] = result["status"]
    case_counts = Counter(workflow_cases.values())
    case_success = {"category": "workflow", "successful_cases": case_counts["passed"], "business_failed_cases": case_counts["failed"], "execution_only_cases": case_counts[None], "total_cases": len(workflow_cases), "value": case_counts["passed"] / len(workflow_cases) if workflow_cases else None, "policy": "Latest completed business result per Case in input order, never best-of-retries. Execution-only cases remain unknown; every execution error stays in execution_reliability and Task Success."}
    errors = Counter(execution_failure_kind(result) for result in results if result["status"] in {"error", "timeout"})
    reliability = {"total_executions": len(results), "completed_executions": counts["passed"] + counts["failed"], "business_failed_executions": counts["failed"], "error_executions": counts["error"], "timeout_executions": counts["timeout"], "value": (counts["passed"] + counts["failed"]) / len(results) if results else None, "by_failure_kind": dict(errors), "provider_call_errors": sum(bool(row.get("error_type")) for result in results for row in result.get("llm_accounting", []))}
    return {"total_runs": len(results), "unique_cases": len({result["case_id"] for result in results}), "passed": counts["passed"], "failed": counts["failed"], "error": counts["error"], "timeout": counts["timeout"], "not_scored": counts["not_scored"], "case_success": case_success, "execution_reliability": reliability, "metrics": metrics, "performance": performance, "failures": [{"case_id": result["case_id"], "variant": result["variant"], "repeat": result["repeat"], "status": result["status"], "failure_kind": execution_failure_kind(result), "reasons": result["failure_reasons"]} for result in results if result["status"] in {"failed", "error", "timeout"}]}


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
