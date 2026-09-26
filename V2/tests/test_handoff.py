import json
import time
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.auth import authenticate
from backend.app.cli import chat_command
from backend.app.database import create_conversation, get_connection
from backend.app.handoff import SupportHandoffRecord, create_support_handoff, get_support_handoff
from backend.app.models import UserContext
from backend.app.support_agent import InvestigationComplete, SupportAgentResult, SupportNextStep, build_support_answer, decide_support_next_step
from backend.app.support_cases import show_case
from backend.app.support_evidence import EvidenceRecord
from backend.app.support_tools import READ_TOOL_SCHEMAS, TOOL_FUNCTIONS, ToolResult, execute_tool_batch, get_order
from backend.app.support_workflow import build_support_workflow, support_agent_node
from simulator.services import common
from simulator.services.common import OrderEvent, payload_hash
from simulator.services.merchant import app as merchant_app
from simulator.services.merchant import store_order_event
from simulator.services.platform import app as platform_app


def create_handoff(seeded_database: dict[str, str], question: str = "订单 O-HANDOFF 在 shop-a 仍然没有进入管理软件") -> tuple[UserContext, str, str]:
    user = authenticate(seeded_database["token_a"])
    conversation_id = create_conversation(user.company_id, user.user_id)
    history = [
        {"role": "user", "content": question},
        {"role": "user", "content": question},
    ]
    _, case_id = create_support_handoff(user, conversation_id, question, history)
    return user, conversation_id, case_id


def test_handoff_atomically_switches_role_and_matches_record_schema(seeded_database: dict[str, str]) -> None:
    user, conversation_id, case_id = create_handoff(seeded_database)
    handoff = get_support_handoff(conversation_id, user)
    with get_connection() as connection:
        role = connection.execute("SELECT active_role FROM support.conversations WHERE conversation_id = %s", (conversation_id,)).fetchone()["active_role"]
        stored_handoff = connection.execute("""SELECT handoff_id::text, conversation_id::text, company_id, customer_problem,
            attempted_steps, known_shop_id, known_order_id, known_sku, missing_fields
            FROM support.handoffs WHERE conversation_id = %s""", (conversation_id,)).fetchone()
        schema_columns = connection.execute("""SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'support' AND table_name = 'handoffs'""").fetchall()
    assert role == "SUPPORT"
    assert handoff.known_shop_id == "shop-a"
    assert handoff.known_order_id == "O-HANDOFF"
    assert handoff.attempted_steps == [handoff.customer_problem]
    assert set(stored_handoff) == set(SupportHandoffRecord.model_fields)
    assert {row["column_name"] for row in schema_columns} == {*SupportHandoffRecord.model_fields, "created_at", "updated_at"}
    assert show_case(case_id, user)["case_id"] == case_id


def test_cross_company_cannot_read_handoff_case(seeded_database: dict[str, str]) -> None:
    _, _, case_id = create_handoff(seeded_database)
    user_b = authenticate(seeded_database["token_b"])
    with pytest.raises(PermissionError, match="not available"):
        show_case(case_id, user_b)


def test_missing_identifier_requests_information_before_model_or_tools(seeded_database: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    user, conversation_id, case_id = create_handoff(seeded_database, "订单 O-HANDOFF 仍然没有进入管理软件")
    handoff = get_support_handoff(conversation_id, user)
    monkeypatch.setattr("backend.app.support_workflow.decide_support_next_step", lambda *args: pytest.fail("planner must not run without shop_id"))
    result = support_agent_node({"question": "继续调查", "user": user.model_dump(), "conversation_id": conversation_id, "case_id": case_id, "handoff": handoff.model_dump(), "evidence": [], "usage": {}, "started_at": 0.0})
    support_decision = SupportNextStep.model_validate(result["support_decision"])
    assert support_decision.next_step == "request_information"
    assert support_decision.missing_information is not None
    assert "shop_id" in support_decision.missing_information.customer_message


def test_internal_contract_distinguishes_scoped_result_and_empty_records(seeded_database: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "read_service_token", lambda name: "support-test-token")
    event = OrderEvent(event_id=str(uuid4()), company_id="company-a", shop_id="shop-a", external_order_id="O-CONTRACT", sku="SKU-1", quantity=1, amount_minor=1000, payment_status="paid")
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO platform.orders (platform_order_id, event_id, company_id, shop_id, external_order_id, sku, quantity, amount_minor, payment_status, payload_hash) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (str(uuid4()), event.event_id, event.company_id, event.shop_id, event.external_order_id, event.sku, event.quantity, event.amount_minor, event.payment_status, payload_hash(event)),
        )
    store_order_event(event)
    headers_a = {"X-Service-Token": "support-test-token", "X-Company-ID": "company-a"}
    headers_b = {"X-Service-Token": "support-test-token", "X-Company-ID": "company-b"}
    assert TestClient(platform_app).get("/internal/orders/O-CONTRACT", params={"shop_id": "shop-a"}, headers=headers_a).status_code == 200
    assert TestClient(platform_app).get("/internal/orders/O-CONTRACT", params={"shop_id": "shop-a"}, headers=headers_b).status_code == 404
    process = TestClient(merchant_app).get("/internal/process-records/O-CONTRACT", params={"shop_id": "shop-a"}, headers=headers_a)
    empty = TestClient(merchant_app).get("/internal/process-records/O-EMPTY", params={"shop_id": "shop-a"}, headers=headers_a)
    assert process.status_code == 200 and process.json()["empty"] is False
    assert empty.status_code == 200 and empty.json()["empty"] is True


def test_tool_reports_service_unavailable_without_inventing_fact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.app.support_tools.read_service_token", lambda name: "support-test-token")

    def unavailable(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr("backend.app.support_tools.httpx.get", unavailable)
    result = get_order(UserContext(company_id="company-a", user_id="admin-a", role="admin"), "shop-a", "O-OFFLINE")
    assert result.status == "unavailable"
    assert result.response["error_type"] == "ConnectError"
    assert result.response["error_code"] == "SERVICE_UNAVAILABLE"
    assert result.response["request_id"]


def test_independent_read_tools_execute_as_parallel_evidence_batch(seeded_database: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    user, _, case_id = create_handoff(seeded_database)

    def fake_order(user: UserContext, shop_id: str, order_id: str) -> ToolResult:
        return ToolResult(tool_name="GetOrder", request={"shop_id": shop_id, "order_id": order_id}, response={"event_id": "event-1"}, source_service="platform", source_record_id="event-1", status="success", latency_ms=3)

    def fake_shop(user: UserContext, shop_id: str) -> ToolResult:
        return ToolResult(tool_name="GetShopStatus", request={"shop_id": shop_id}, response={"shop_id": shop_id, "sync_enabled": False}, source_service="merchant", source_record_id=shop_id, status="success", latency_ms=2)

    monkeypatch.setitem(TOOL_FUNCTIONS, "GetOrder", fake_order)
    monkeypatch.setitem(TOOL_FUNCTIONS, "GetShopStatus", fake_shop)
    calls = [
        {"name": "GetOrder", "args": {"shop_id": "shop-a", "order_id": "O-HANDOFF"}, "id": "call-order"},
        {"name": "GetShopStatus", "args": {"shop_id": "shop-a"}, "id": "call-shop"},
    ]
    evidence = execute_tool_batch(case_id, user, calls, "shop-a", "O-HANDOFF")
    assert len(evidence) == 2
    assert all(record.parallel for record in evidence)
    assert {record.model_tool_call_id for record in evidence} == {"call-order", "call-shop"}
    assert evidence[0].request and evidence[0].response


def test_support_role_is_sticky_and_does_not_return_to_customer_graph(seeded_database: dict[str, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _, conversation_id, case_id = create_handoff(seeded_database)
    result = SupportAgentResult(answer="已读取真实证据。", status="diagnosed", case_id=case_id, evidence_ids=["evidence-1"], tool_path=["GetOrder"], tool_call_count=1, usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})
    monkeypatch.setattr("backend.app.conversations.run_support_workflow", lambda *args: result)
    monkeypatch.setattr("backend.app.conversations.run_customer_workflow", lambda *args: pytest.fail("Customer workflow must not run after handoff"))
    chat_command(seeded_database["token_a"], "继续调查", conversation_id, None)
    assert "Active role: SUPPORT" in capsys.readouterr().out


def test_tool_budget_stops_without_another_model_call(seeded_database: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    user, conversation_id, case_id = create_handoff(seeded_database)
    handoff = get_support_handoff(conversation_id, user)
    evidence = [EvidenceRecord(evidence_id=str(uuid4()), sequence=index + 1, batch_id=str(uuid4()), parallel=False, tool_name="GetOrder", request={"shop_id": "shop-a", "order_id": f"O-{index}"}, response={"event_id": str(index)}, source_service="platform", status="success", latency_ms=1) for index in range(6)]
    monkeypatch.setattr("backend.app.support_workflow.decide_support_next_step", lambda *args: pytest.fail("planner must not run after budget"))
    result = support_agent_node({"question": "继续", "user": user.model_dump(), "conversation_id": conversation_id, "case_id": case_id, "handoff": handoff.model_dump(), "evidence": [record.model_dump() for record in evidence], "usage": {}, "started_at": 0.0})
    support_decision = SupportNextStep.model_validate(result["support_decision"])
    assert support_decision.next_step == "human_support"
    assert support_decision.human_support is not None
    assert "预算" in support_decision.human_support.reason


def test_support_agent_binds_only_registered_query_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    bound_tools: list[type] = []

    class FakeResponse:
        def __init__(self) -> None:
            self.tool_calls = [{"name": "GetOrder", "args": {"shop_id": "shop-a", "order_id": "O-1"}, "id": "call-1"}]
            self.content = ""
            self.usage_metadata = {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6}

    class FakeBoundModel:
        def bind_tools(self, tools):
            bound_tools.extend(tools)
            return self

        def invoke(self, messages):
            return FakeResponse()

    def fake_google_model(*, max_retries: int):
        assert max_retries == 2
        return FakeBoundModel()

    monkeypatch.setattr("backend.app.support_agent.create_google_model", fake_google_model)
    handoff = SupportHandoffRecord(
        handoff_id="handoff-1",
        conversation_id="conversation-1",
        company_id="company-a",
        customer_problem="Order did not sync",
        known_shop_id="shop-a",
        known_order_id="O-1",
    )
    support_decision, usage = decide_support_next_step("Investigate", handoff, [], 6)
    assert bound_tools == READ_TOOL_SCHEMAS
    assert {tool.__name__ for tool in bound_tools}.isdisjoint({"InvestigationComplete", "MissingInformationRequest", "HumanSupportRequired"})
    assert support_decision.next_step == "use_tool"
    assert support_decision.tool_calls[0]["name"] == "GetOrder"
    assert usage["total_tokens"] == 6


def test_support_agent_parses_finish_data_without_control_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    bound_tools: list[type] = []

    class FakeResponse:
        def __init__(self) -> None:
            self.tool_calls = []
            self.content = json.dumps({"next_step": "finish", "investigation_complete": {"summary": "已确认", "confirmed_facts": [{"text": "平台存在订单", "evidence_ids": ["evidence-1"]}]}})
            self.usage_metadata = {"input_tokens": 5, "output_tokens": 3, "total_tokens": 8}

    class FakeBoundModel:
        def bind_tools(self, tools):
            bound_tools.extend(tools)
            return self

        def invoke(self, messages):
            return FakeResponse()

    def fake_google_model(*, max_retries: int):
        return FakeBoundModel()

    monkeypatch.setattr("backend.app.support_agent.create_google_model", fake_google_model)
    handoff = SupportHandoffRecord(
        handoff_id="handoff-1",
        conversation_id="conversation-1",
        company_id="company-a",
        customer_problem="Order did not sync",
        known_shop_id="shop-a",
        known_order_id="O-1",
    )
    support_decision, usage = decide_support_next_step("Investigate", handoff, [], 6)
    assert bound_tools == READ_TOOL_SCHEMAS
    assert support_decision.next_step == "finish"
    assert support_decision.investigation_complete is not None
    assert support_decision.investigation_complete.summary == "已确认"
    assert usage["total_tokens"] == 8


def test_support_workflow_executes_query_tool_then_returns_to_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    handoff = SupportHandoffRecord(handoff_id="handoff-1", conversation_id="conversation-1", company_id="company-a", customer_problem="Order did not sync", known_shop_id="shop-a", known_order_id="O-1")
    model_calls = 0

    def fake_support_decision(*args):
        nonlocal model_calls
        model_calls += 1

        if model_calls == 1:
            return SupportNextStep(next_step="use_tool", tool_calls=[{"name": "GetOrder", "args": {"shop_id": "shop-a", "order_id": "O-1"}, "id": "call-1"}]), {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}

        investigation_complete = InvestigationComplete(summary="调查完成", confirmed_facts=[{"text": "平台存在订单", "evidence_ids": ["evidence-1"]}])
        return SupportNextStep(next_step="finish", investigation_complete=investigation_complete), {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}

    def fake_execute_tool_batch(*args):
        return [EvidenceRecord(evidence_id="evidence-1", sequence=1, batch_id="batch-1", parallel=False, model_tool_call_id="call-1", tool_name="GetOrder", request={"shop_id": "shop-a", "order_id": "O-1"}, response={"event_id": "event-1"}, source_service="platform", status="success", latency_ms=1)]

    monkeypatch.setattr("backend.app.support_workflow.decide_support_next_step", fake_support_decision)
    monkeypatch.setattr("backend.app.support_workflow.execute_tool_batch", fake_execute_tool_batch)
    workflow = build_support_workflow().compile()
    result = workflow.invoke({"question": "继续调查", "user": UserContext(company_id="company-a", user_id="user-a", role="admin").model_dump(), "conversation_id": "conversation-1", "case_id": "case-1", "handoff": handoff.model_dump(), "evidence": [], "usage": {}, "started_at": time.perf_counter()})
    assert model_calls == 2
    assert result["status"] == "diagnosed"
    assert "平台存在订单" in result["answer"]


def test_conflicting_shipment_evidence_cannot_be_rendered_as_confirmed_root_cause() -> None:
    first_id = str(uuid4())
    second_id = str(uuid4())
    records = [
        EvidenceRecord(evidence_id=first_id, sequence=1, batch_id=str(uuid4()), parallel=True, tool_name="GetShipment", request={"shop_id": "shop-a", "order_id": "O-1"}, response={"shipment_id": "S-1", "tracking_number": "TRACK-A"}, source_service="warehouse", status="success", latency_ms=1, object_type="shipment", object_id="O-1"),
        EvidenceRecord(evidence_id=second_id, sequence=2, batch_id=str(uuid4()), parallel=True, tool_name="GetPlatformShipment", request={"shop_id": "shop-a", "order_id": "O-1"}, response={"shipment_id": "S-1", "tracking_number": "TRACK-B"}, source_service="platform", status="success", latency_ms=1, object_type="shipment", object_id="O-1"),
    ]
    investigation_complete = InvestigationComplete.model_validate({"summary": "已确认", "confirmed_facts": [{"text": "平台正确", "evidence_ids": [first_id, second_id]}]})
    support_decision = SupportNextStep(next_step="finish", investigation_complete=investigation_complete)
    answer, status = build_support_answer(support_decision, records)
    assert status == "pending_human"
    assert "矛盾" in answer
