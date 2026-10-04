"""One isolated evaluation case, using real API ingress and simulator HTTP services."""
import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from openai import APITimeoutError

from backend.app.api import app
from backend.app.auth import authenticate
from backend.app.database import create_conversation, get_connection
from backend.app.handoff import create_support_handoff
from evals.judge import JudgeError, empty_answer_scores, judge_rag, judge_workflow
from evals.metrics import applicable_metrics, claim_scores, rag_retrieval_scores, tool_scores, workflow_action_check, workflow_business_claim_check, workflow_result_check
from evals.observe import Observer
from evals.runtime import configure_tokens
from evals.scenarios import arm_fault, business_snapshot, mutate_facts, reset_case, seed_case


def safe_error(error: Exception) -> str:
    text = str(error)
    for name in ("OPENROUTER_API_KEY", "EMBEDDING_API_KEY", "DATABASE_URL"):
        secret = os.getenv(name)
        if secret:
            text = text.replace(secret, "[redacted]")
    return text[:3000]


def start_worker(directory: Path) -> tuple[subprocess.Popen, object]:
    log = (directory / "worker.log").open("a", encoding="utf-8")
    process = subprocess.Popen([sys.executable, "-m", "evals.runtime", "--service", "worker"], stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    return process, log


def stop_worker(worker: tuple) -> None:
    process, log = worker
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    log.close()


def post(client: TestClient, tokens: dict, user_id: str, path: str, body: dict | None = None) -> dict:
    response = client.post(path, headers={"Authorization": "Bearer " + tokens[user_id]}, json=body)
    return {"http_status": response.status_code, "body": response.json()}


def run_agent(case: dict, variant: str, initial: dict, tokens: dict, output: dict, client: TestClient) -> None:
    user_id = case["permissions"]["user_id"]
    user = authenticate(tokens[user_id])
    conversation = create_conversation(user.company_id, user.user_id)
    question = case["question"]
    if case["category"] == "workflow" and initial["scenario"] not in {"missing_identifiers", "human_request", "customer_backend"}:
        question = initial["message"] + " " + question
    if case["category"] == "workflow" and case["initial_state"]["start_role"] == "SUPPORT":
        create_support_handoff(user, conversation, question, [])
    output["conversation_id"] = conversation
    output["actual_question"] = question
    result = post(client, tokens, user_id, f"/api/v1/conversations/{conversation}/messages", {"question": question, "retrieval_mode": variant if case["category"] == "rag" else "vector_only"})
    output["api_responses"].append(result)
    if result["http_status"] != 200:
        raise RuntimeError(f"Application returned HTTP {result['http_status']}: {result['body']}")
    output["turn"] = result["body"]["turn"]
    output["snapshot"] = result["body"]["snapshot"]


def run_action(case: dict, initial: dict, tokens: dict, output: dict, client: TestClient, worker_state: list, directory: Path) -> None:
    user_id = case["permissions"]["user_id"]
    actor = case["permissions"]["actor_user_id"]
    user = authenticate(tokens[user_id])
    operation = case["initial_state"]["operation"]
    conversation = create_conversation(user.company_id, user.user_id)
    shop = "shop-b" if operation == "wrong_shop" else initial["shop_id"]
    question = f"{shop} 的 SKU-1 库存不一致" if initial["scenario"] == "inventory_mismatch" else f"{shop} 的订单 {initial['order_id']} 没有同步"
    _, case_id = create_support_handoff(user, conversation, question, [])
    output.update({"conversation_id": conversation, "support_case_id": case_id, "actual_question": question})
    request = {"action_type": case["expected_action"], "enable_order_sync": case["initial_state"]["enable_order_sync"], "enable_shipment_sync": case["initial_state"]["enable_shipment_sync"]}
    plan_response = post(client, tokens, user_id, f"/api/v1/cases/{case_id}/actions", request)
    output["api_responses"].append(plan_response)
    if plan_response["http_status"] != 200:
        output["plan_rejected"] = True
        return
    plan = plan_response["body"]
    output["plan"] = plan
    action_id = plan.get("action_id")
    if not action_id:
        raise RuntimeError("Fixture expected a stored action plan")
    output["action_id"] = action_id
    output["mutation"] = mutate_facts(operation, initial, action_id)
    output["side_effect_baseline"] = business_snapshot(initial)
    if operation in {"response_lost", "unknown_before_accept"}:
        resource = "orders" if case["expected_action"] == "retry_order_sync" else "shipments"
        mode = "drop_after_accept" if operation == "response_lost" else "drop_before_accept"
        arm_fault(mode, "/repairs/" + resource)
        output["fault"] = {"mode": mode, "path": "/repairs/" + resource}
    if operation == "worker_restart":
        stop_worker(worker_state[0])
        worker_state.clear()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(post, client, tokens, actor, f"/api/v1/actions/{action_id}/decision", {"decision": "approve"})
            table = "order_recovery_tasks" if case["expected_action"] == "retry_order_sync" else "shipment_recovery_tasks"
            receipt_table = "order_repair_receipts" if table == "order_recovery_tasks" else "shipment_repair_receipts"
            deadline = time.monotonic() + 5
            interrupted = None
            while time.monotonic() < deadline:
                with get_connection() as connection:
                    interrupted = connection.execute(f"UPDATE merchant.{table} t SET status = 'processing' FROM merchant.{receipt_table} r WHERE t.receipt_id = r.receipt_id AND r.action_id = %s RETURNING t.task_id::text", (action_id,)).fetchone()
                if interrupted:
                    break
                time.sleep(0.05)
            if not interrupted:
                raise RuntimeError("Worker restart fault could not be initialized")
            output["fault"] = {"mode": "persisted_processing_state_then_real_worker_restart", "task_id": interrupted["task_id"], "limitation": "Models a committed interrupted task; does not measure process-kill timing inside a transaction."}
            worker_state.append(start_worker(directory))
            output["api_responses"].append(future.result())
    else:
        endpoint = "execute" if operation in {"missing_approval", "missing_decision"} else "decision"
        body = None if endpoint == "execute" else {"decision": "approve"}
        output["api_responses"].append(post(client, tokens, actor, f"/api/v1/actions/{action_id}/{endpoint}", body))
    if operation in {"response_lost", "unknown_before_accept"}:
        with get_connection() as connection:
            execution = connection.execute("SELECT status, claim_until FROM support.action_executions WHERE action_id = %s", (action_id,)).fetchone()
        output["execution_after_fault"] = execution
        output["business_after_fault"] = business_snapshot(initial)
        output["api_responses"].append(post(client, tokens, actor, f"/api/v1/actions/{action_id}/execute"))
        if operation == "unknown_before_accept":
            from datetime import UTC, datetime

            remaining = (execution["claim_until"] - datetime.now(UTC)).total_seconds()
            time.sleep(max(0, remaining) + 0.05)
            output["api_responses"].append(post(client, tokens, actor, f"/api/v1/actions/{action_id}/execute"))
    if operation in {"duplicate_submit", "duplicate_confirmation"}:
        endpoint = "execute" if operation == "duplicate_submit" else "decision"
        body = None if endpoint == "execute" else {"decision": "approve"}
        output["api_responses"].append(post(client, tokens, actor, f"/api/v1/actions/{action_id}/{endpoint}", body))
    output["action_details"] = post_get_action(client, tokens, user_id, action_id)


def post_get_action(client: TestClient, tokens: dict, user_id: str, action_id: str) -> dict:
    response = client.get(f"/api/v1/actions/{action_id}", headers={"Authorization": "Bearer " + tokens[user_id]})
    response.raise_for_status()
    return response.json()


def score_observed_tools_and_retrieval(case: dict, output: dict) -> None:
    observations = output["observations"]
    if case["category"] == "rag":
        with get_connection() as connection:
            rows = connection.execute("SELECT c.chunk_id::text, d.source_uri FROM support.document_chunks c JOIN support.product_documents d ON c.document_id = d.document_id WHERE d.source_uri = ANY(%s) AND d.company_id = %s", (case["retrieval_ground_truth"], case["permissions"]["company_id"])).fetchall()
            runs = connection.execute("SELECT query, search_query, filters, mode, chunk_ids, vector_ranks, keyword_ranks, fused_ranks, rerank_ranks, rerank_error, latency_ms FROM support.retrieval_runs WHERE conversation_id = %s ORDER BY created_at", (output["conversation_id"],)).fetchall()
        if {row["source_uri"] for row in rows} != set(case["retrieval_ground_truth"]):
            raise RuntimeError("Retrieval ground truth sources were not ingested")
        output["retrieval_runs"] = runs
        retrievals = observations["retrieval"]
        chunks = retrievals[-1].get("chunks", []) if retrievals else []
        scored = rag_retrieval_scores(chunks, case)
        output["retrieval_evidence"] = scored.pop("retrieval_evidence")
        output["covered_source_groups"] = scored.pop("covered_source_groups")
        output["metrics"].update(scored)
    elif case["category"] == "workflow":
        output["metrics"].update(tool_scores(observations["selected_tools"], case["expected_tools"], output["initial"]))


def score_agent(case: dict, output: dict) -> None:
    turn = output["turn"]
    observations = output["observations"]
    handoff = bool(turn.get("ticket_id") or turn.get("needs_support") or (case["category"] == "workflow" and case["initial_state"]["start_role"] == "CUSTOMER" and turn.get("active_role") == "SUPPORT"))
    acceptable_handoffs = case["expected"].get("acceptable_handoffs", [case["expected_handoff"]])
    output["metrics"]["handoff_accuracy"] = float(handoff in acceptable_handoffs)
    score_observed_tools_and_retrieval(case, output)
    if case["category"] == "rag":
        before, after = observations["before_claims"], observations["after_claims"]
        output["metrics"].update(empty_answer_scores(before, turn["answer"]))
        chunks = [chunk for item in observations["retrieval"] for chunk in item.get("chunks", [])]
        judged = judge_rag(case, before, after, turn["answer"], chunks)
        output["independent_judge"] = judged
        output["metrics"].update(claim_scores(before, after, judged["judgment"], turn["answer"]))
        judgment = judged["judgment"]
        output["failure_reasons"].extend("Missing core fact: " + item["reason"] for item in judgment.get("fact_checks", []) if item.get("core_required", True) and not item.get("core_passed", item["passed"]))
        output["failure_reasons"].extend("Forbidden Claim: " + claim for claim in judgment.get("forbidden_claims", []))
        output["failure_reasons"].extend("Unsupported Claim: " + claim for claim in judgment.get("unsupported_final_claims", []))
        if not case["expected"]["answerable"]:
            output["no_answer_correct"] = judgment["no_answer_correct"] and judgment["answer_correct_after"]
    else:
        status = "support_transfer" if turn.get("needs_support") else turn.get("status")
        action = (turn.get("action_plan") or {}).get("action_type")
        text = turn["answer"].casefold()
        action_check = workflow_action_check(case, output)
        claim_check = workflow_business_claim_check(case, turn["answer"], output)
        result_check = workflow_result_check(output)
        output["task_result_check"] = result_check
        output["failure_reasons"].extend(result_check["violations"])
        output["business_claim_check"] = claim_check
        for violation in claim_check["violations"]:
            prefix = "Unsupported specific cause: " if violation["rule"] in {"unsupported_specific_cause", "unsupported_causal_explanation"} else "Unsupported business promise: "
            output["failure_reasons"].append(prefix + violation["text"])
        diagnosis = status in case["expected"]["statuses"] and action_check["passed"] and claim_check["passed"] is not False and result_check["passed"] and any(pattern.casefold() in text for pattern in case["expected"]["diagnosis_any"])
        output["metrics"]["diagnosis_accuracy"] = float(diagnosis)
        output["diagnosis_scoring"] = {"method": "human_authored_status_action_and_critical_claim_rules_v2", "actual_status": status, "actual_action": action, "action_check": action_check, "limitation": "Concept and targeted claim rules are smoke checks; nuanced diagnosis needs independent human adjudication."}
        if case.get("workflow_ground_truth") and output.get("initial"):
            output["metrics"].pop("diagnosis_accuracy", None)
            judged = judge_workflow(case, output)
            output["independent_judge"] = judged
            judgment = judged["judgment"]
            # The semantic contract replaces keyword/cause heuristics, not the frozen answers.
            output["failure_reasons"] = [reason for reason in output["failure_reasons"] if not reason.startswith(("Unsupported specific cause:", "Unsupported business promise:"))]
            output["business_claim_check"] = {"checked": True, "passed": not judgment["unsupported_claims"], "violations": [{"rule": "semantic_unsupported_claim", "text": claim} for claim in judgment["unsupported_claims"]]}
            output["metrics"]["diagnosis_accuracy"] = float(judgment["diagnosis_correct"] and all(item["passed"] for item in judgment["fact_checks"]))
            output["metrics"]["handoff_accuracy"] *= float(judgment["handoff_valid"])
            names = {tool.get("name") for tool in observations.get("selected_tools", [])}
            routes = case["workflow_ground_truth"]["required_tool_routes"]
            route_complete = any(set(route).issubset(names) for route in routes)
            if not route_complete:
                output["metrics"]["diagnosis_accuracy"] = 0.0
                output["failure_reasons"].append("Missing Tool: required backend facts were not established; customer statements are not verified evidence")
                if turn.get("ticket_id"):
                    output["metrics"]["handoff_accuracy"] = 0.0
                judged.setdefault("deterministic_overrides", {})["required_route_incomplete"] = True
            if judgment["unnecessary_tool_calls"]:
                output["metrics"]["tool_selection_accuracy"] = 0.0
                output["failure_reasons"].append("Unnecessary Tool: " + str(judgment["unnecessary_tool_calls"]))
            if status not in case["expected"]["statuses"]:
                output["failure_reasons"].append("Unexpected workflow outcome: " + str(status))
            if not action_check["passed"] or not judgment["action_valid"]:
                output["failure_reasons"].append("Invalid Action: actual action or prerequisites do not satisfy the contract")
            output["failure_reasons"].extend("Unsupported Claim: " + claim for claim in judgment["unsupported_claims"])
            output["failure_reasons"].extend("Incomplete Action: " + action for action in judgment["incomplete_actions"])
            output["failure_reasons"].extend("Missing fact: " + item["reason"] for item in judgment["fact_checks"] if not item["passed"])
            output["diagnosis_scoring"].update(method=judged["method"], fact_checks=judgment["fact_checks"], action_check={"passed": action_check["passed"] and judgment["action_valid"], "deterministic": action_check})
        if output["after_business"]["counts"] != output["before_business"]["counts"] or output["after_business"]["business_rows"] != output["before_business"]["business_rows"]:
            output["failure_reasons"].append("Read-only investigation produced an unexpected business effect")


def score_action(case: dict, output: dict) -> None:
    before, after = output["before_business"], output["after_business"]
    expected = case["expected_business_state"]
    field = expected["resolved_field"]
    completed = bool(field and after[field])
    details = output.get("action_details", {})
    statuses = [response["body"].get("status") for response in output["api_responses"] if isinstance(response["body"], dict)]
    reported_success = "verified_resolved" in statuses or details.get("status") == "verified_resolved"
    baseline = output.get("side_effect_baseline", before)
    no_effect = baseline["counts"] == after["counts"] and baseline["business_rows"] == after["business_rows"]
    repair_tables = ("merchant.order_repair_receipts", "merchant.shipment_repair_receipts", "merchant.action_repair_receipts")
    duplicate = after["order_count"] > 1 or after["shipment_count"] > 1 or any(after["counts"][name] - before["counts"][name] > 1 for name in repair_tables)
    duplicate = duplicate or after["counts"]["merchant.orders"] - before["counts"]["merchant.orders"] > 1 or after["counts"]["platform.shipments"] - before["counts"]["platform.shipments"] > 1
    kind = case["expected"]["kind"]
    final_response = output["api_responses"][-1]
    denied = final_response["http_status"] in {401, 403, 409} or final_response["body"].get("status") in {"blocked", "expired", "proposed", "rejected"}
    if output.get("plan_rejected"):
        denied = kind == "invalid" and final_response["http_status"] == 409
    name = {"unauthorized": "unauthorized_action_blocking_rate", "invalid": "invalid_action_rejection_rate", "valid": "valid_action_completion_rate", "recovery": "recovery_success_rate"}[kind]
    output["metrics"].update({name: float(no_effect and denied) if kind in {"unauthorized", "invalid"} else float(completed and reported_success and not duplicate), "duplicate_business_effect_rate": float(duplicate), "false_success_rate": float(reported_success and not completed)})
    if kind in {"valid", "recovery"} and any(response["http_status"] >= 400 for response in output["api_responses"]):
        output["failure_reasons"].append("A valid execution request returned an HTTP error")
    if case["initial_state"]["operation"] in {"response_lost", "unknown_before_accept"} and (output.get("execution_after_fault") or {}).get("status") != "unknown":
        output["failure_reasons"].append("Fault did not create the intended unknown execution state")


def evaluate(case: dict, variant: str, directory: Path, output_path: Path) -> dict:
    configure_tokens()
    output = {"case_id": case["case_id"], "category": case["category"], "variant": variant, "case": case, "status": "error", "phase": "initializing", "applicable_metrics": applicable_metrics(case), "metrics": {}, "performance": {}, "failure_reasons": [], "api_responses": [], "model_execution": "real_llm" if case["category"] in {"rag", "workflow"} else "not_used", "mock_used": False}
    directory.mkdir(parents=True, exist_ok=True)
    worker_state = []
    observer = Observer(directory / "observations.json")
    started = None
    try:
        arm_fault(None, None)
        tokens = reset_case(directory)
        worker_state.append(start_worker(directory))
        initial = seed_case(case)
        output["initial"] = initial
        output["before_business"] = business_snapshot(initial)
        user = authenticate(tokens[case["permissions"]["user_id"]])
        if user.company_id != case["permissions"]["company_id"] or user.role != case["permissions"]["role"]:
            raise ValueError("Dataset permissions disagree with authenticated simulator identity")
        output["phase"] = "executing_application"
        output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        with observer, TestClient(app, raise_server_exceptions=True) as client:
            started = time.perf_counter()
            output["application_started_monotonic"] = started
            output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            if case["category"] in {"rag", "workflow"}:
                run_agent(case, variant, initial, tokens, output, client)
            else:
                run_action(case, initial, tokens, output, client, worker_state, directory)
            output["performance"]["end_to_end_latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
        output["observations"] = observer.snapshot()
        output["performance"].update(output["observations"]["performance"])
        output["after_business"] = business_snapshot(initial)
        output["phase"] = "scoring"
        output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        if case["category"] in {"rag", "workflow"}:
            score_agent(case, output)
        else:
            score_action(case, output)
        for name in output["applicable_metrics"]:
            if case["category"] == "rag" and name in {"recall_at_5", "mrr"}:
                continue
            if name in {"answer_accuracy_before", "unsupported_claim_rate_before"}:
                continue
            value = output["metrics"].get(name)
            if value is None:
                output["failure_reasons"].append("Missing metric evidence: " + name)
            elif name in {"duplicate_business_effect_rate", "false_success_rate"} and value:
                output["failure_reasons"].append(name + " observed")
            elif isinstance(value, dict) and name.startswith("unsupported_claim_rate") and value["numerator"] > 0:
                output["failure_reasons"].append(name + " observed unsupported claims")
            elif isinstance(value, dict) and name == "supported_claim_retention_rate" and value["numerator"] < value["denominator"]:
                output["failure_reasons"].append(name + " dropped supported claims")
            elif isinstance(value, (int, float)) and name not in {"duplicate_business_effect_rate", "false_success_rate", "mrr"} and value < 1:
                output["failure_reasons"].append(name + " did not meet smoke expectation")
        output["status"] = "failed" if output["failure_reasons"] else "passed"
    except Exception as error:  # noqa: BLE001 - preserve every application, setup and judge failure
        if isinstance(error, JudgeError):
            output["independent_judge"] = error.audit
            output["scoring_status"] = "judge_error"
            output["metrics"].update(error.audit.get("deterministic_metrics", {}))
        output["error_type"] = type(error).__name__
        timeout_error = error.__cause__ if isinstance(error, JudgeError) else error
        output["status"] = "timeout" if isinstance(timeout_error, (TimeoutError, httpx.TimeoutException, APITimeoutError)) else "error"
        output["failure_reasons"].append(type(error).__name__ + ": " + safe_error(error))
        output["observations"] = observer.snapshot()
        output["performance"].update(output["observations"]["performance"])
        if started is not None and "end_to_end_latency_ms" not in output["performance"]:
            output["performance"]["end_to_end_latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
        if "initial" in output:
            try:
                output["after_business"] = business_snapshot(output["initial"])
                score_observed_tools_and_retrieval(case, output)
            except Exception as snapshot_error:  # noqa: BLE001 - missing business evidence must be visible
                output["business_readback_error"] = type(snapshot_error).__name__
    finally:
        for worker in worker_state:
            stop_worker(worker)
        output["phase"] = "finished"
        output["model_execution"] = "real_llm" if observer.calls else "not_used" if started is not None else "not_executed"
        output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--variant", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evaluate(json.loads(args.case.read_text(encoding="utf-8")), args.variant, args.output.parent, args.output)
