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


@pytest.mark.parametrize("problem", [None, "wrong_event", "wrong_quantity", "wrong_amount", "missing_order", "duplicate_order", "empty_sku", "wrong_scope", "read_error", "shipment_question"])
def test_completed_order_stops_after_complete_matching_or_conflicting_business_evidence(problem):
    from backend.app.handoff import SupportHandoffRecord
    from backend.app.support_workflow import has_confirmed_business_blocker

    handoff = SupportHandoffRecord(handoff_id="h", conversation_id="c", company_id="a", customer_problem="请核对订单是否已导入", known_shop_id="shop-a", known_order_id="ORDER-1")
    source = {"event_id": "event-1", "payment_status": "paid", "sku": "SKU-1", "quantity": 2, "amount_minor": 20000}
    processing = {"event_id": "event-1", "task_status": "completed", "merchant_order_count": 1, "merchant_order_id": "merchant-1", "merchant_sku": "MERCHANT-1", "platform_sku": "SKU-1", "quantity": 2, "amount_minor": 20000}
    if problem == "wrong_event":
        processing["event_id"] = "event-other"
    if problem == "wrong_quantity":
        processing["quantity"] = 3
    if problem == "wrong_amount":
        processing["amount_minor"] = 1
    if problem == "missing_order":
        processing["merchant_order_id"] = None
    if problem == "duplicate_order":
        processing["merchant_order_count"] = 2
    if problem == "empty_sku":
        processing["merchant_sku"] = None
    if problem == "shipment_question":
        handoff.customer_problem = "订单已导入，但发货状态未更新"
    request = {"shop_id": "shop-a", "order_id": "ORDER-1"}
    records = [EvidenceRecord(evidence_id="order", sequence=1, batch_id="b", parallel=False, tool_name="GetOrder", request=request, response=source, source_service="platform", status="success", latency_ms=1), EvidenceRecord(evidence_id="process", sequence=2, batch_id="b", parallel=False, tool_name="GetOrderProcessRecords", request=dict(request), response=processing, source_service="merchant", status="success", latency_ms=1)]
    if problem == "wrong_scope":
        records[1].request["order_id"] = "ORDER-OTHER"
    if problem == "read_error":
        records[1].status = "error"
    assert has_confirmed_business_blocker(records, handoff) is (problem in {None, "wrong_event", "wrong_quantity", "wrong_amount"})


@pytest.mark.parametrize("next_step", ["finish", "human_support"])
@pytest.mark.parametrize("guess", ["具体原因目前无法确认：可能由网络故障、平台维护引起。", "映射缺失的原因（商品未创建、配置未完成等）。", "Worker 可能是系统故障或资源问题。", "原因可能是缓存过期。"])
def test_final_answers_and_handoffs_do_not_list_unknown_causes(next_step, guess):
    from backend.app.support_diagnosis import ClaimWithEvidence, HumanSupportRequired, build_support_answer

    record = EvidenceRecord(evidence_id="task", sequence=1, batch_id="b", parallel=False, tool_name="GetWorkerTask", request={}, response={"error_code": "WORKER_INTERRUPTED"}, source_service="merchant", status="success", latency_ms=1)
    facts = [ClaimWithEvidence(text="WORKER_INTERRUPTED", evidence_ids=["task"])]
    if next_step == "finish":
        decision = SupportNextStep(next_step="finish", investigation_complete=InvestigationComplete(summary="Worker 中断", confirmed_facts=facts, unknowns=[guess, "恢复时间未知"]))
    else:
        decision = SupportNextStep(next_step="human_support", human_support=HumanSupportRequired(reason="需要人工继续调查", known_facts=facts, unknowns=[guess, "恢复时间未知"]))
    answer, status = build_support_answer(decision, [record])
    assert guess not in answer
    assert "具体原因目前无法确认" in answer
    assert "恢复时间未知" in answer
    assert status == ("diagnosed" if next_step == "finish" else "pending_human")


@pytest.mark.parametrize("next_step", ["finish", "human_support"])
def test_specific_cause_needs_direct_backend_cause_evidence(next_step):
    from backend.app.support_diagnosis import ClaimWithEvidence, HumanSupportRequired, build_support_answer

    record = EvidenceRecord(evidence_id="task", sequence=1, batch_id="b", parallel=False, tool_name="GetWorkerTask", request={}, response={"error_code": "WORKER_INTERRUPTED"}, source_service="merchant", status="success", latency_ms=1)
    def decision():
        if next_step == "finish":
            return SupportNextStep(next_step="finish", investigation_complete=InvestigationComplete(summary="根因是网络故障", confirmed_facts=[ClaimWithEvidence(text="任务已中断", evidence_ids=["task"])]))
        return SupportNextStep(next_step="human_support", human_support=HumanSupportRequired(reason="根因是网络故障"))
    answer, _ = build_support_answer(decision(), [record])
    assert "网络故障" not in answer
    record.response["root_cause"] = "网络故障"
    answer, _ = build_support_answer(decision(), [record])
    assert "根因是网络故障" in answer


def test_citing_error_status_is_not_proof_of_a_possible_cause():
    from backend.app.support_diagnosis import ClaimWithEvidence, build_investigation_answer

    record = EvidenceRecord(evidence_id="task", sequence=1, batch_id="b", parallel=False, tool_name="GetWorkerTask", request={}, response={"error_code": "WORKER_INTERRUPTED"}, source_service="merchant", status="success", latency_ms=1)
    decision = InvestigationComplete(summary="任务已中断", confirmed_facts=[ClaimWithEvidence(text="WORKER_INTERRUPTED", evidence_ids=["task"])], possible_causes=[ClaimWithEvidence(text="磁盘已满", evidence_ids=["task"])])
    answer, status = build_investigation_answer(decision, [record])
    assert "磁盘已满" not in answer
    assert "具体原因目前无法确认" in answer
    assert status == "diagnosed"


@pytest.mark.parametrize("status", ["success", "error"])
def test_stock_diagnosis_keeps_backend_code_without_inventing_root_cause(status):
    from backend.app.support_diagnosis import ClaimWithEvidence, build_investigation_answer

    record = EvidenceRecord(evidence_id="stock", sequence=1, batch_id="b", parallel=False, tool_name="GetStockStatus", request={}, response={"assessment": "difference", "reason": "VERSION_NOT_PUBLISHED"}, source_service="merchant", status=status, latency_ms=1)
    decision = InvestigationComplete(summary="具体原因目前无法确认", confirmed_facts=[ClaimWithEvidence(text="平台版本与仓库版本不同", evidence_ids=["stock"])], unknowns=["可能由网络故障引起"])
    answer, _ = build_investigation_answer(decision, [record])
    assert ("VERSION_NOT_PUBLISHED" in answer) is (status == "success")
    assert "网络故障" not in answer


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


@pytest.mark.parametrize("condition", ["success", "empty", "not_found", "error", "timeout", "different_arguments", "local_rejection"])
def test_attempted_identical_query_is_not_bound_again_but_other_requests_remain_available(monkeypatch, condition):
    from langchain_core.messages import AIMessage

    from backend.app.handoff import SupportHandoffRecord
    from backend.app.support_diagnosis import decide_support_next_step

    bound_tools = []

    class Model:
        def bind_tools(self, schemas):
            bound_tools.extend(schema.__name__ for schema in schemas)
            return self

        def invoke(self, messages, **kwargs):
            return AIMessage(content='{"next_step":"human_support","human_support":{"reason":"Unknown facts"}}')

    monkeypatch.setattr("backend.app.support_diagnosis.create_model", lambda **kwargs: Model())
    request = {"shop_id": "shop-a", "order_id": "O-1"}
    status = condition if condition in {"success", "empty", "not_found", "error"} else "success"
    if condition in {"timeout", "local_rejection"}:
        status = "error"
    if condition == "different_arguments":
        request = {"shop_id": "shop-a", "order_id": "O-other"}
    response = {"error_code": "ORDER_SCOPE_MISMATCH"} if condition == "local_rejection" else {}
    if condition == "timeout":
        response = {"error_code": "TIMEOUT"}
    source = "support" if condition == "local_rejection" else "merchant"
    record = EvidenceRecord(evidence_id="task", sequence=1, batch_id="b", parallel=False, tool_name="GetWorkerTask", request=request, response=response, source_service=source, status=status, latency_ms=1)
    handoff = SupportHandoffRecord(handoff_id="h", conversation_id="c", company_id="a", customer_problem="Investigate", known_shop_id="shop-a", known_order_id="O-1")
    primary = [record.model_copy(update={"tool_name": name, "request": {"shop_id": "shop-a", "order_id": "O-1"}, "status": "success", "source_service": "merchant", "response": {}}) for name in ["GetOrder", "GetOrderProcessRecords"]]
    decide_support_next_step("Investigate", handoff, [*primary, record], 3)
    assert ("GetWorkerTask" in bound_tools) is (condition in {"different_arguments", "local_rejection"})
    assert "GetShopConnectionStatus" in bound_tools
    assert "GetOrderProcessRecords" not in bound_tools


def test_duplicate_requests_in_same_batch_execute_only_once(monkeypatch):
    call = {"name": "GetOrder", "args": {"shop_id": "shop-a", "order_id": "O-1"}}
    state = {"question": "Investigate", "handoff": {"handoff_id": "h", "conversation_id": "c", "company_id": "a", "customer_problem": "Order failed", "known_shop_id": "shop-a", "known_order_id": "O-1"}, "evidence": [], "started_at": time.perf_counter(), "usage": {}}
    monkeypatch.setattr("backend.app.support_workflow.decide_support_next_step", lambda *args: (SupportNextStep(next_step="use_tool", tool_calls=[call, call]), {}))
    result = decide_support_next_step_node(state)
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["args"] == call["args"]


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


@pytest.mark.parametrize("error_code", ["CHANNEL_AUTH_EXPIRED", "SKU_MAPPING_MISSING"])
def test_missing_order_sku_prompt_preserves_explicit_backend_error(monkeypatch, error_code):
    from langchain_core.messages import AIMessage

    from backend.app.handoff import SupportHandoffRecord
    from backend.app.support_diagnosis import decide_support_next_step

    record = EvidenceRecord(evidence_id="process", sequence=1, batch_id="b", parallel=False, tool_name="GetOrderProcessRecords", request={"shop_id": "shop-a", "order_id": "O-1"}, response={"merchant_order_id": None, "merchant_sku": None, "task_status": "blocked", "error_code": error_code}, source_service="merchant", status="success", latency_ms=1)
    original = record.model_dump()
    messages_seen = []

    def model(messages, **kwargs):
        messages_seen.extend(messages)
        return AIMessage(content='{"next_step":"finish","investigation_complete":{"summary":"未生成商家订单","confirmed_facts":[]}}')

    monkeypatch.setattr("backend.app.support_diagnosis.call_support_model", model)
    handoff = SupportHandoffRecord(handoff_id="h", conversation_id="c", company_id="a", customer_problem="Investigate", known_shop_id="shop-a", known_order_id="O-1")
    decide_support_next_step("Investigate", handoff, [record], 5)
    assert "merchant_sku 来自商家订单记录，不是 SKU 映射表" in messages_seen[0].content
    assert "CHANNEL_AUTH_EXPIRED / 401 表示授权阻塞，不表示映射缺失" in messages_seen[0].content
    assert error_code in messages_seen[1].content
    assert '"merchant_sku": null' in messages_seen[1].content
    assert record.model_dump() == original


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
    expected = condition in {"mapping", "outage", "missing_order"}
    assert has_confirmed_business_blocker(records, handoff) is expected
    calls = []

    def decide(question, target, evidence, remaining):
        calls.append(remaining)
        return SupportNextStep(next_step="finish", investigation_complete=InvestigationComplete(summary="Observed facts", confirmed_facts=[])), {}

    monkeypatch.setattr("backend.app.support_workflow.decide_support_next_step", decide)
    state = {"question": "Investigate", "handoff": handoff.model_dump(), "evidence": [record.model_dump() for record in records], "started_at": time.perf_counter(), "usage": {}}
    result = decide_support_next_step_node(state)
    if condition == "missing_order":
        assert calls == []
        assert result["support_next_step"]["next_step"] == "request_information"
    else:
        assert calls == [0 if expected else 6 - len(records)]


@pytest.mark.parametrize("error_code", ["WORKER_INTERRUPTED", "TRANSIENT_PROCESSING_ERROR"])
@pytest.mark.parametrize("condition", ["ready", "failed", "processing", "not_retryable", "wrong_task", "wrong_event", "wrong_order", "local_error", "sync_disabled", "unpaid", "updated_task", "shipment_question"])
def test_confirmed_retryable_order_failure_stops_only_with_complete_matching_facts(monkeypatch, condition, error_code):
    from backend.app.handoff import SupportHandoffRecord
    from backend.app.support_workflow import has_confirmed_business_blocker

    request = {"shop_id": "shop-a", "order_id": "O-1"}
    status = "failed" if condition == "failed" or error_code == "TRANSIENT_PROCESSING_ERROR" else "processing"
    if condition == "processing":
        status = "processing"
    responses = {"GetOrder": {"event_id": "event-1", "payment_status": "paid"}, "GetOrderProcessRecords": {"event_id": "event-1", "task_id": "task-1", "task_status": status, "error_code": error_code, "merchant_order_id": None, "merchant_order_count": 0}, "GetWorkerTask": {"event_id": "event-1", "task_id": "task-1", "status": status, "error_code": error_code, "retryable": True}, "GetShopSyncStatus": {"sync_enabled": True}, "GetShopConnectionStatus": {"connection_status": "authorized"}}
    records = []
    for number, (name, response) in enumerate(responses.items(), 1):
        args = {"shop_id": "shop-a"} if name in {"GetShopSyncStatus", "GetShopConnectionStatus"} else request
        records.append(EvidenceRecord(evidence_id=name, sequence=number, batch_id="b", parallel=False, tool_name=name, request=args, response=response, source_service="platform" if name == "GetOrder" else "merchant", status="success", latency_ms=1))
    task = records[2]
    if condition == "not_retryable":
        task.response["retryable"] = False
    elif condition == "wrong_task":
        task.response["task_id"] = "task-2"
    elif condition == "wrong_event":
        task.response["event_id"] = "event-2"
    elif condition == "wrong_order":
        task.request = {"shop_id": "shop-a", "order_id": "O-2"}
    elif condition == "local_error":
        task.source_service = "support"
        task.status = "error"
    elif condition == "sync_disabled":
        records[3].response["sync_enabled"] = False
    elif condition == "unpaid":
        records[0].response["payment_status"] = "unpaid"
    elif condition == "updated_task":
        records.append(task.model_copy(update={"evidence_id": "updated", "sequence": 6, "response": dict(task.response, retryable=False)}))
    problem = "Investigate shipment" if condition == "shipment_question" else "Worker interrupted"
    handoff = SupportHandoffRecord(handoff_id="h", conversation_id="c", company_id="a", customer_problem=problem, known_shop_id="shop-a", known_order_id="O-1")
    expected = condition in {"ready", "failed", "unpaid"} or (condition == "processing" and error_code == "WORKER_INTERRUPTED")
    assert has_confirmed_business_blocker(records, handoff) is expected
    if expected:
        calls = []
        monkeypatch.setattr("backend.app.support_workflow.decide_support_next_step", lambda question, context, evidence, remaining: (calls.append(remaining) or SupportNextStep(next_step="finish", investigation_complete=InvestigationComplete(summary="Worker interrupted", confirmed_facts=[])), {}))
        state = {"question": problem, "handoff": handoff.model_dump(), "evidence": [record.model_dump() for record in records], "started_at": time.perf_counter(), "usage": {}}
        result = decide_support_next_step_node(state)
        assert calls == [0]
        assert result["tool_calls"] == []


@pytest.mark.parametrize("condition", ["expired", "recovered", "different_error", "completed"])
def test_confirmed_auth_blocker_does_not_repeat_connection_queries(condition):
    from backend.app.handoff import SupportHandoffRecord
    from backend.app.support_workflow import has_confirmed_business_blocker

    request = {"shop_id": "shop-a", "order_id": "O-1"}
    facts = {"task_status": "completed" if condition == "completed" else "blocked", "error_code": "SKU_MAPPING_MISSING" if condition == "different_error" else "CHANNEL_AUTH_EXPIRED"}
    connection = {"connection_status": "authorized" if condition == "recovered" else "auth_expired"}
    records = [EvidenceRecord(evidence_id="order", sequence=1, batch_id="b", parallel=False, tool_name="GetOrder", request=request, response={}, source_service="platform", status="success", latency_ms=1), EvidenceRecord(evidence_id="process", sequence=2, batch_id="b", parallel=False, tool_name="GetOrderProcessRecords", request=request, response=facts, source_service="merchant", status="success", latency_ms=1), EvidenceRecord(evidence_id="connection", sequence=3, batch_id="b", parallel=False, tool_name="GetShopConnectionStatus", request={"shop_id": "shop-a"}, response=connection, source_service="merchant", status="success", latency_ms=1)]
    handoff = SupportHandoffRecord(handoff_id="h", conversation_id="c", company_id="a", customer_problem="Authorization expired", known_shop_id="shop-a", known_order_id="O-1")
    assert has_confirmed_business_blocker(records, handoff) is (condition == "expired")
