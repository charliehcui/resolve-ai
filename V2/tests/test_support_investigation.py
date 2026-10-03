"""Focused investigation regressions; synthetic decisions, no database or LLM calls."""
import time

import pytest

from backend.app.handoff import extract_support_ids, find_missing_support_ids
from backend.app.support_diagnosis import InvestigationComplete, SupportNextStep
from backend.app.support_evidence import EvidenceRecord
from backend.app.support_workflow import decide_support_next_step_node


@pytest.fixture(scope="session", autouse=True)
def isolated_test_database():
    yield


def test_order_identifier_does_not_invent_a_sku_or_require_inventory_information():
    question = "shop-demo-mapping 的订单 O-MISSING-SKU-MAPPING-12345678 商品映射缺失"
    shop, order, sku = extract_support_ids(question)
    assert (shop, order, sku) == ("shop-demo-mapping", "O-MISSING-SKU-MAPPING-12345678", None)
    assert find_missing_support_ids(shop, order, sku, question) == []
    question = "shop-demo-inventory 的 SKU-1 库存不一致"
    shop, order, sku = extract_support_ids(question)
    assert (shop, order, sku) == ("shop-demo-inventory", None, "SKU-1")
    assert find_missing_support_ids(shop, order, sku, question) == []


@pytest.mark.parametrize("budget", ["time", "tools"])
def test_read_budget_exhaustion_allows_one_terminal_decision_without_new_reads(monkeypatch, budget):
    records = [EvidenceRecord(evidence_id=f"e-{index}", sequence=index, batch_id="b", parallel=False, tool_name="GetOrder", request={}, response={}, source_service="platform", status="success", latency_ms=1).model_dump() for index in range(6 if budget == "tools" else 1)]
    state = {"question": "Investigate", "handoff": {"handoff_id": "h", "conversation_id": "c", "company_id": "a", "customer_problem": "Order failed"}, "evidence": records, "started_at": time.perf_counter() - (30 if budget == "time" else 0), "usage": {}}
    attempts = []

    def terminal(question, handoff, evidence, remaining):
        attempts.append(remaining)
        return SupportNextStep(next_step="finish", investigation_complete=InvestigationComplete(summary="Observed facts", confirmed_facts=[])), {}

    monkeypatch.setattr("backend.app.support_workflow.decide_support_next_step", terminal)
    result = decide_support_next_step_node(state)
    assert attempts == [0]
    assert result["support_next_step"]["next_step"] == "finish"
    assert result["tool_calls"] == []
    monkeypatch.setattr("backend.app.support_workflow.decide_support_next_step", lambda *args: (SupportNextStep(next_step="use_tool", tool_calls=[{"name": "GetOrder", "args": {"shop_id": "shop-a", "order_id": "O-1"}}]), {}))
    result = decide_support_next_step_node(state)
    assert result["support_next_step"]["next_step"] == "human_support"
    assert result["tool_calls"] == []


def test_task_success_keeps_workflow_failures_errors_and_timeouts_in_denominator():
    from evals.metrics import summarize

    results = [{"case_id": str(index), "category": "workflow", "variant": "current", "repeat": 1, "status": status, "applicable_metrics": [], "metrics": {}, "performance": {}, "failure_reasons": []} for index, status in enumerate(("passed", "failed", "error", "timeout"))]
    score = summarize(results)["metrics"]["task_success_rate"]
    assert score["value"] == 0.25
    assert score["denominator"] == 4


def test_terminal_model_call_has_json_output_and_no_read_tools(monkeypatch):
    from langchain_core.messages import AIMessage

    from backend.app.support_diagnosis import call_support_model

    calls = []

    class Model:
        def bind_tools(self, *args, **kwargs):
            raise AssertionError("Terminal decision must not bind read tools")

        def invoke(self, messages, **kwargs):
            calls.append(kwargs)
            return AIMessage(content='{"next_step":"human_support","human_support":{"reason":"Insufficient facts"}}')

    monkeypatch.setattr("backend.app.support_diagnosis.create_model", lambda **kwargs: Model())
    call_support_model([], terminal_only=True)
    assert calls == [{"response_format": {"type": "json_object"}}]


def test_repeated_read_gets_one_terminal_decision_without_executing_duplicate(monkeypatch):
    record = EvidenceRecord(evidence_id="e", sequence=1, batch_id="b", parallel=False, tool_name="GetStockStatus", request={"shop_id": "shop-a", "sku": "SKU-1"}, response={}, source_service="merchant", status="success", latency_ms=1)
    state = {"question": "Investigate stock", "handoff": {"handoff_id": "h", "conversation_id": "c", "company_id": "a", "customer_problem": "Stock differs"}, "evidence": [record.model_dump()], "started_at": time.perf_counter(), "usage": {}}
    attempts = []

    def decide(question, handoff, evidence, remaining):
        attempts.append(remaining)
        if remaining:
            return SupportNextStep(next_step="use_tool", tool_calls=[{"name": record.tool_name, "args": record.request}]), {}
        return SupportNextStep(next_step="finish", investigation_complete=InvestigationComplete(summary="Observed stock differs", confirmed_facts=[])), {}

    monkeypatch.setattr("backend.app.support_workflow.decide_support_next_step", decide)
    result = decide_support_next_step_node(state)
    assert attempts == [5, 0]
    assert result["support_next_step"]["next_step"] == "finish"
    assert result["tool_calls"] == []


@pytest.mark.parametrize("order,sku,required,excluded", [("O-1", None, "GetOrderProcessRecords", "GetStockStatus"), (None, "SKU-1", "GetStockStatus", "GetWorkerTask")])
def test_model_only_receives_tools_with_known_identifiers(monkeypatch, order, sku, required, excluded):
    from langchain_core.messages import AIMessage

    from backend.app.handoff import SupportHandoffRecord
    from backend.app.support_diagnosis import decide_support_next_step

    tools = []

    class Model:
        def bind_tools(self, schemas):
            tools.extend(schema.__name__ for schema in schemas)
            return self

        def invoke(self, messages, **kwargs):
            return AIMessage(content='{"next_step":"human_support","human_support":{"reason":"Needs investigation"}}')

    monkeypatch.setattr("backend.app.support_diagnosis.create_model", lambda **kwargs: Model())
    handoff = SupportHandoffRecord(handoff_id="h", conversation_id="c", company_id="a", customer_problem="Investigate", known_shop_id="shop-a", known_order_id=order, known_sku=sku)
    decide_support_next_step("Investigate", handoff, [], 6)
    assert required in tools
    assert excluded not in tools


@pytest.mark.parametrize("next_step", ["finish", "human_support"])
def test_local_validation_error_cannot_support_business_claims_but_real_mapping_fact_can(monkeypatch, next_step):
    import json

    from langchain_core.messages import AIMessage

    from backend.app.handoff import SupportHandoffRecord
    from backend.app.support_diagnosis import decide_support_next_step

    records = [EvidenceRecord(evidence_id="local-error", sequence=1, batch_id="b", parallel=False, tool_name="GetStockStatus", request={"sku": "SKU-UNKNOWN"}, response={"error_code": "SKU_SCOPE_MISMATCH"}, source_service="support", status="error", latency_ms=0), EvidenceRecord(evidence_id="business-fact", sequence=2, batch_id="b", parallel=False, tool_name="GetOrderProcessRecords", request={}, response={"error_code": "SKU_MAPPING_MISSING", "task_status": "blocked", "merchant_sku": None}, source_service="merchant", status="success", latency_ms=1)]
    claims = [{"text": "SKU invalid", "evidence_ids": ["local-error"]}, {"text": "SKU mapping missing", "evidence_ids": ["business-fact"]}]
    field = "investigation_complete" if next_step == "finish" else "human_support"
    payload = {"summary": "Mapping blocked", "confirmed_facts": claims} if next_step == "finish" else {"reason": "Mapping blocked", "known_facts": claims}
    calls = []

    def model(messages, **kwargs):
        calls.append(messages)
        return AIMessage(content=json.dumps({"next_step": next_step, field: payload}))

    monkeypatch.setattr("backend.app.support_diagnosis.call_support_model", model)
    handoff = SupportHandoffRecord(handoff_id="h", conversation_id="c", company_id="a", customer_problem="Mapping", known_shop_id="shop-a", known_order_id="O-1")
    decision, _ = decide_support_next_step("Investigate", handoff, records, 4)
    result = getattr(decision, field)
    remaining = result.confirmed_facts if next_step == "finish" else result.known_facts
    assert [claim.text for claim in remaining] == ["SKU mapping missing"]
    assert any("SKU_SCOPE_MISMATCH" in unknown for unknown in result.unknowns)
    assert '"evidence_kind": "argument_validation_error"' in calls[0][1].content
    assert '"evidence_kind": "backend_response"' in calls[0][1].content
    assert records[0].response == {"error_code": "SKU_SCOPE_MISMATCH"}


def test_answer_cannot_treat_local_argument_rejection_as_business_fact():
    from backend.app.support_diagnosis import ClaimWithEvidence, build_investigation_answer

    record = EvidenceRecord(evidence_id="local-error", sequence=1, batch_id="b", parallel=False, tool_name="GetStockStatus", request={}, response={"error_code": "SKU_SCOPE_MISMATCH"}, source_service="support", status="error", latency_ms=0)
    decision = InvestigationComplete(summary="SKU invalid", confirmed_facts=[ClaimWithEvidence(text="SKU invalid", evidence_ids=["local-error"])])
    answer, status = build_investigation_answer(decision, [record])
    assert status == "pending_human"
    assert "SKU invalid" not in answer


@pytest.mark.parametrize("condition", ["mapping", "outage", "local_rejection", "missing_order", "different_order", "recovered", "updated_processing", "stock_request"])
def test_confirmed_blocker_stops_reads_only_with_matching_backend_facts(monkeypatch, condition):
    from backend.app.handoff import SupportHandoffRecord
    from backend.app.support_workflow import has_confirmed_business_blocker

    request = {"shop_id": "shop-a", "order_id": "O-1"}
    mapping = condition in {"mapping", "local_rejection", "updated_processing", "stock_request"}
    processing = {"error_code": "SKU_MAPPING_MISSING", "task_status": "blocked", "merchant_sku": None} if mapping else {"error_code": "CHANNEL_UNAVAILABLE", "task_status": "failed"}
    records = [EvidenceRecord(evidence_id="order", sequence=1, batch_id="b", parallel=False, tool_name="GetOrder", request=request, response={"external_order_id": "O-1"}, source_service="platform", status="success", latency_ms=1), EvidenceRecord(evidence_id="processing", sequence=2, batch_id="b", parallel=False, tool_name="GetOrderProcessRecords", request=request, response=processing, source_service="merchant", status="success", latency_ms=1), EvidenceRecord(evidence_id="connection", sequence=3, batch_id="b", parallel=False, tool_name="GetShopConnectionStatus", request={"shop_id": "shop-a"}, response={"connection_status": "unavailable"}, source_service="merchant", status="success", latency_ms=1)]
    if condition == "local_rejection":
        records[1].source_service = "support"
        records[1].status = "error"
    elif condition == "missing_order":
        records[0].status = "not_found"
    elif condition == "different_order":
        records[1].request = {"shop_id": "shop-a", "order_id": "O-other"}
    elif condition == "recovered":
        records.append(records[2].model_copy(update={"evidence_id": "new-connection", "sequence": 4, "response": {"connection_status": "authorized"}}))
    elif condition == "updated_processing":
        records.append(records[1].model_copy(update={"evidence_id": "new-processing", "sequence": 4, "response": {"error_code": None, "task_status": "done", "merchant_sku": "SKU-1"}}))
    handoff = SupportHandoffRecord(handoff_id="h", conversation_id="c", company_id="a", customer_problem="Order investigation", known_shop_id="shop-a", known_order_id="O-1", known_sku="SKU-1" if condition == "stock_request" else None)
    expected = condition in {"mapping", "outage"}
    assert has_confirmed_business_blocker(records, handoff) is expected
    calls = []

    def decide(question, target, evidence, remaining):
        calls.append(remaining)
        return SupportNextStep(next_step="finish", investigation_complete=InvestigationComplete(summary="Observed facts", confirmed_facts=[])), {}

    monkeypatch.setattr("backend.app.support_workflow.decide_support_next_step", decide)
    state = {"question": "Investigate", "handoff": handoff.model_dump(), "evidence": [record.model_dump() for record in records], "started_at": time.perf_counter(), "usage": {}}
    decide_support_next_step_node(state)
    assert calls == [0 if expected else 6 - len(records)]
