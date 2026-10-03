"""Small synthetic routing and investigation regressions; no model benchmarks."""
import pytest

from backend.app.handoff import SupportHandoffRecord
from backend.app.support_diagnosis import CandidateAction, ClaimWithEvidence, InvestigationComplete, SupportNextStep, primary_read_tools
from backend.app.support_evidence import EvidenceRecord
from backend.app.support_workflow import build_investigation_answer_node, has_confirmed_business_blocker
from backend.app.tickets import is_human_request
from backend.app.user_intent import action_request, is_paused


@pytest.fixture(scope="session", autouse=True)
def isolated_test_database():
    yield


@pytest.mark.parametrize("text, expected", [("不要转人工，只解释当前事实", False), ("我不需要人工，请核对订单", False), ("Please do not contact an engineer; inspect the order", False), ("如果仍无法核实，再转人工", False), ("为什么需要人工处理？", False), ("请转人工工程师", True), ("不需要重试，请转人工", True), ("先不需要人工，但现在请联系工程师", True)])
def test_human_routing_uses_scoped_directives_and_current_intent(text, expected):
    assert is_human_request(text) is expected


@pytest.mark.parametrize("text, recovery, paused", [("暂时不用处理", False, True), ("暂时不用处理，但请核对当前状态", False, False), ("不要重试，先说明已有事实", False, False), ("不要转人工，请重试订单", True, False)])
def test_pausing_or_refusing_one_action_does_not_refuse_another(text, recovery, paused):
    assert action_request(text, "recovery") is recovery
    assert bool(is_paused(text)) is paused


def record(name, response, status="success", object_type=None):
    return EvidenceRecord(evidence_id=name, sequence=1, batch_id="b", parallel=False, tool_name=name, request={"shop_id": "shop-a", "order_id": "O-1"}, response=response, source_service="merchant", status=status, object_type=object_type, latency_ms=1)


def test_primary_queries_include_process_and_requested_current_setting_before_auxiliary_reads():
    handoff = SupportHandoffRecord(handoff_id="h", conversation_id="c", company_id="a", customer_problem="调查订单", known_shop_id="shop-a", known_order_id="O-1")
    assert primary_read_tools(handoff, "请核对当前设置，不用历史错误推断") == {"GetOrder", "GetOrderProcessRecords", "GetShopSyncStatus"}
    assert primary_read_tools(handoff, "请核对发货信息") == {"GetWarehouseShipment", "GetShipmentProcessRecords", "GetPlatformShipment"}


@pytest.mark.parametrize("conflict, wrong_scope, expected", [(True, False, True), (False, False, True), (True, True, False)])
def test_decisive_shipment_evidence_stops_auxiliary_investigation(conflict, wrong_scope, expected):
    handoff = SupportHandoffRecord(handoff_id="h", conversation_id="c", company_id="a", customer_problem="请核对发货", known_shop_id="shop-a", known_order_id="O-1")
    shipment = {"shipment_id": "s", "carrier": "express", "tracking_number": "t", "shipment_count": 1}
    records = [record("GetWarehouseShipment", shipment, object_type="shipment"), record("GetPlatformShipment", dict(shipment, status="shipped", tracking_number="other" if conflict else "t"), object_type="shipment"), record("GetShipmentProcessRecords", dict(shipment, task_status="completed"), object_type="shipment")]
    if wrong_scope:
        records[1].request["order_id"] = "OTHER"
    assert has_confirmed_business_blocker(records, handoff) is expected


def test_declined_recovery_cannot_create_a_model_recommended_plan(monkeypatch):
    decision = InvestigationComplete(summary="建议重试订单", confirmed_facts=[ClaimWithEvidence(text="当前任务失败", evidence_ids=["GetOrderProcessRecords"])], recommended_action=CandidateAction(action_type="retry_order_sync", reason="失败", evidence_ids=["GetOrderProcessRecords"]))
    state = {"question": "不要重试，先核对已有事实", "support_next_step": SupportNextStep(next_step="finish", investigation_complete=decision).model_dump(), "handoff": {"handoff_id": "h", "conversation_id": "c", "company_id": "a", "customer_problem": "调查失败订单", "known_shop_id": "shop-a", "known_order_id": "O-1"}, "evidence": [record("GetOrderProcessRecords", {"task_status": "failed"}).model_dump()]}
    monkeypatch.setattr("backend.app.support_workflow.create_action_plan", lambda *args: pytest.fail("A declined action must not create a plan"))
    result = build_investigation_answer_node(state)
    assert result["status"] == "diagnosed" and result["action_plan"] is None


def test_paused_conversation_does_not_investigate_or_create_ticket(monkeypatch):
    from backend.app.conversations import process_conversation_message
    from backend.app.models import UserContext
    monkeypatch.setattr("backend.app.conversations.authorize_conversation", lambda *args: {"active_role": "SUPPORT"})
    monkeypatch.setattr("backend.app.conversations.load_messages", lambda *args: [])
    monkeypatch.setattr("backend.app.conversations.save_message", lambda *args: None)
    monkeypatch.setattr("backend.app.conversations.save_agent_run", lambda *args: "r")
    monkeypatch.setattr("backend.app.conversations.create_engineer_ticket", lambda *args: pytest.fail("No ticket during pause"))
    monkeypatch.setattr("backend.app.conversations.run_support_workflow", lambda *args: pytest.fail("No investigation during pause"))
    user = UserContext(user_id="u", company_id="a", role="staff", name="test")
    result = process_conversation_message(user, "暂时不用处理", "c")
    assert result["status"] == "diagnosed" and not result.get("ticket_id")
