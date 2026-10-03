"""Focused synthetic regressions; no model calls, not benchmark evidence."""
import time

import pytest

from backend.app.handoff import SupportHandoffRecord
from backend.app.support_diagnosis import ClaimWithEvidence, HumanSupportRequired, InvestigationComplete, SupportNextStep, build_human_support_answer, ground_cause_text
from backend.app.support_evidence import EvidenceRecord
from backend.app.support_workflow import build_investigation_answer_node, has_confirmed_business_blocker, missing_order_recovery_candidate
from evals.metrics import summarize, workflow_business_claim_check, workflow_result_check


@pytest.fixture(scope="session", autouse=True)
def isolated_test_database():
    yield


def missing_order_records():
    records = []
    values = [("GetOrder", "success", {"payment_status": "paid", "event_id": "event-1", "sku": "SKU-1", "quantity": 2, "amount_minor": 2000}), ("GetOrderProcessRecords", "empty", {"empty": True, "receipt": None, "task": None, "merchant_order": None}), ("GetShopSyncStatus", "success", {"sync_enabled": True}), ("GetShopConnectionStatus", "success", {"connection_status": "authorized"})]
    for index, (name, status, response) in enumerate(values):
        request = {"shop_id": "shop-a"}
        if name in {"GetOrder", "GetOrderProcessRecords"}:
            request["order_id"] = "ORDER-1"
        records.append(EvidenceRecord(evidence_id=str(index), sequence=index, batch_id="b", parallel=False, tool_name=name, request=request, response=response, source_service="merchant", status=status, latency_ms=1))
    return records


@pytest.mark.parametrize("problem", [None, "unpaid", "read_error", "sync_off", "unauthorized", "active_task", "wrong_scope", "diagnose_only"])
def test_missing_receipt_recovery_needs_current_complete_evidence(problem):
    records = missing_order_records()
    handoff = SupportHandoffRecord(handoff_id="h", conversation_id="c", company_id="a", customer_problem="请调查订单是否可以恢复", known_shop_id="shop-a", known_order_id="ORDER-1")
    if problem == "unpaid":
        records[0].response["payment_status"] = "unpaid"
    elif problem == "read_error":
        records[1].status = "error"
    elif problem == "sync_off":
        records[2].response["sync_enabled"] = False
    elif problem == "unauthorized":
        records[3].response["connection_status"] = "auth_expired"
    elif problem == "active_task":
        records[1].response["task"] = {"status": "pending"}
    elif problem == "wrong_scope":
        records[0].request["order_id"] = "ORDER-OTHER"
    elif problem == "diagnose_only":
        handoff.customer_problem = "只读核对，不要创建修复计划"
    candidate = missing_order_recovery_candidate(records, handoff)
    assert (candidate is not None) is (problem is None)
    if candidate:
        assert candidate.action_type == "retry_order_sync"
        assert {"0", "1"}.issubset(candidate.evidence_ids)


def test_missing_receipt_creates_real_plan_path_instead_of_only_talking(monkeypatch):
    records = missing_order_records()
    decision = InvestigationComplete(summary="需要人工检查未接收的订单", confirmed_facts=[ClaimWithEvidence(text="平台订单已付款", evidence_ids=["0"])])
    state = {"support_next_step": SupportNextStep(next_step="finish", investigation_complete=decision).model_dump(), "handoff": {"handoff_id": "h", "conversation_id": "c", "company_id": "a", "customer_problem": "恢复缺失订单", "known_shop_id": "shop-a", "known_order_id": "ORDER-1"}, "evidence": [record.model_dump() for record in records], "user": {"user_id": "u", "company_id": "a", "role": "staff", "name": "test"}, "case_id": "case-1"}
    calls = []
    def create_plan(user, case_id, candidate):
        calls.append(candidate)
        return {"status": "proposed", "action_type": candidate.action_type, "approval_requirement": "user_confirmation"}
    monkeypatch.setattr("backend.app.support_workflow.create_action_plan", create_plan)
    monkeypatch.setattr("backend.app.support_workflow.load_evidence", lambda *args: records)
    result = build_investigation_answer_node(state)
    assert result["status"] == "awaiting_confirmation" and len(calls) == 1
    assert result["action_plan"]["action_type"] == "retry_order_sync"


@pytest.mark.parametrize("problem", [None, "sync_off", "current_read_error", "wrong_order"])
def test_restored_channel_stops_only_with_current_healthy_evidence(problem):
    records = missing_order_records()
    records[1].status = "success"
    records[1].response = {"task_status": "blocked", "error_code": "CHANNEL_AUTH_EXPIRED", "merchant_order_count": 0, "merchant_order_id": None}
    handoff = SupportHandoffRecord(handoff_id="h", conversation_id="c", company_id="a", customer_problem="授权已恢复，请调查历史订单", known_shop_id="shop-a", known_order_id="ORDER-1")
    if problem == "sync_off":
        records[2].response["sync_enabled"] = False
    elif problem == "current_read_error":
        records[3].status = "error"
    elif problem == "wrong_order":
        records[1].request["order_id"] = "ORDER-OTHER"
    assert has_confirmed_business_blocker(records, handoff) is (problem is None)


def test_human_recommendation_changes_status_to_real_ticket_path():
    decision = InvestigationComplete(summary="需要人工检查来源缺失", confirmed_facts=[])
    state = {"support_next_step": SupportNextStep(next_step="finish", investigation_complete=decision).model_dump(), "handoff": {"handoff_id": "h", "conversation_id": "c", "company_id": "a", "customer_problem": "调查来源"}, "evidence": []}
    assert build_investigation_answer_node(state)["status"] == "pending_human"


@pytest.mark.parametrize("text", ["库存差异的原因是平台版本未发布到仓库。", "因为版本不同，平台维护导致同步失败。", "The failure is due to resource exhaustion."])
def test_correlation_cannot_become_cause_in_answers_or_handoffs(text):
    record = missing_order_records()[0]
    record.response = {"source_version": 2, "warehouse_version": 1}
    assert ground_cause_text(text, [record]) == "具体原因目前无法确认"
    decision = HumanSupportRequired(reason=text, known_facts=[ClaimWithEvidence(text="平台来源版本为 2，仓库版本为 1", evidence_ids=[record.evidence_id])])
    answer, status = build_human_support_answer(decision, [record])
    assert text not in answer and "平台来源版本为 2" in answer and status == "pending_human"
    output = {"snapshot": {"case": {"evidence": [record.model_dump()]}}}
    assert not workflow_business_claim_check({"claim_ground_truth": {}}, text, output)["passed"]


def test_case_success_and_execution_errors_have_different_denominators():
    results = []
    for case_id, status, reason in [("a", "error", "OpenAIRateLimitError: 429"), ("a", "passed", ""), ("b", "passed", ""), ("b", "failed", ""), ("c", "error", "Primary endpoint is unavailable")]:
        results.append({"case_id": case_id, "category": "workflow", "variant": "current", "repeat": 1, "status": status, "failure_reasons": [reason] if reason else [], "performance": {}, "metrics": {}, "applicable_metrics": []})
    summary = summarize(results)
    assert summary["case_success"]["successful_cases"] == 1
    assert summary["case_success"]["business_failed_cases"] == 1
    assert summary["case_success"]["execution_only_cases"] == 1
    assert summary["case_success"]["total_cases"] == 3
    assert summary["execution_reliability"]["total_executions"] == 5
    assert summary["execution_reliability"]["by_failure_kind"] == {"provider_error": 1, "preflight_error": 1}
    assert summary["metrics"]["task_success_rate"]["denominator"] == 5


@pytest.mark.parametrize("status, answer", [("diagnosed", "建议转人工检查。"), ("pending_human", "需要人工处理。"), ("awaiting_confirmation", "建议执行 retry_order_sync。")])
def test_text_or_returned_id_without_database_record_does_not_complete_task(status, answer):
    result = {"turn": {"status": status, "answer": answer, "ticket_id": "fabricated-ticket", "action_plan_id": "fabricated-plan", "action_plan": {"action_id": "fabricated-plan", "status": "proposed"}}, "snapshot": {"ticket": None, "actions": []}}
    assert not workflow_result_check(result)["passed"]
