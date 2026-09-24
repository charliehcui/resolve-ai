from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.auth import authenticate
from backend.app.cli import chat_command
from backend.app.config import get_settings
from backend.app.database import create_conversation, get_connection
from backend.app.handoff import SupportHandoff, handoff_to_support, load_handoff
from backend.app.models import UserContext
from backend.app.support_agent import SupportInvestigationResult, plan_support_step, render_control_result
from backend.app.support_cases import show_case
from backend.app.support_evidence import EvidenceRecord
from backend.app.support_tools import TOOL_FUNCTIONS, ToolResult, execute_tool_batch, get_order
from backend.app.support_workflow import support_plan_node
from simulator.services import common
from simulator.services.common import OrderEvent, payload_hash
from simulator.services.merchant import app as merchant_app
from simulator.services.merchant import store_order_event
from simulator.services.platform import app as platform_app


def create_handoff(seeded_database: dict[str, str], question: str = "订单 O-HANDOFF 在 shop-a 仍然没有进入管理软件") -> tuple[UserContext, str, str]:
    user = authenticate(seeded_database["token_a"])
    conversation_id = create_conversation(user.company_id, user.user_id)
    history = [{"role": "assistant", "content": "请先确认订单同步开关。", "metadata": {"citations": [{"chunk_id": "chunk-1", "title": "订单同步", "source_uri": "docs/product/01-order-sync-switch.md"}]}}]
    _, case_id = handoff_to_support(user, conversation_id, question, "需要后台调查。", history)
    return user, conversation_id, case_id


def test_handoff_atomically_switches_role_and_preserves_customer_context(seeded_database: dict[str, str]) -> None:
    user, conversation_id, case_id = create_handoff(seeded_database)
    handoff = load_handoff(conversation_id, user)
    with get_connection() as connection:
        role = connection.execute("SELECT active_role FROM support.conversations WHERE conversation_id = %s", (conversation_id,)).fetchone()["active_role"]
    assert role == "SUPPORT"
    assert handoff.known_shop_id == "shop-a"
    assert handoff.known_order_id == "O-HANDOFF"
    assert handoff.customer_answer == "请先确认订单同步开关。"
    assert handoff.citations[0].source_uri == "docs/product/01-order-sync-switch.md"
    assert show_case(case_id, user)["case_id"] == case_id


def test_cross_company_cannot_read_handoff_case(seeded_database: dict[str, str]) -> None:
    _, _, case_id = create_handoff(seeded_database)
    auth_b = authenticate(seeded_database["token_b"])
    with pytest.raises(PermissionError, match="not available"):
        show_case(case_id, auth_b)


def test_missing_identifier_requests_information_before_model_or_tools(seeded_database: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    user, conversation_id, case_id = create_handoff(seeded_database, "订单 O-HANDOFF 仍然没有进入管理软件")
    handoff = load_handoff(conversation_id, user)
    monkeypatch.setattr("backend.app.support_workflow.plan_support_step", lambda *args: pytest.fail("planner must not run without shop_id"))
    result = support_plan_node({"question": "继续调查", "user": user.model_dump(), "conversation_id": conversation_id, "case_id": case_id, "handoff": handoff.model_dump(), "evidence": [], "usage": {}, "started_at": 0.0})
    assert result["status"] == "needs_info"
    assert "shop_id" in result["answer"]


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
    result = SupportInvestigationResult(answer="已读取真实证据。", status="diagnosed", case_id=case_id, evidence_ids=["evidence-1"], tool_path=["GetOrder"], tool_call_count=1, usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})
    monkeypatch.setattr("backend.app.conversations.run_support_graph", lambda *args: result)
    monkeypatch.setattr("backend.app.conversations.run_customer_workflow", lambda *args: pytest.fail("Customer workflow must not run after handoff"))
    chat_command(seeded_database["token_a"], "继续调查", conversation_id, None)
    assert "Active role: SUPPORT" in capsys.readouterr().out


def test_tool_budget_stops_without_another_model_call(seeded_database: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    user, conversation_id, case_id = create_handoff(seeded_database)
    handoff = load_handoff(conversation_id, user)
    evidence = [EvidenceRecord(evidence_id=str(uuid4()), sequence=index + 1, batch_id=str(uuid4()), parallel=False, tool_name="GetOrder", request={"shop_id": "shop-a", "order_id": f"O-{index}"}, response={"event_id": str(index)}, source_service="platform", status="success", latency_ms=1) for index in range(6)]
    monkeypatch.setattr("backend.app.support_workflow.plan_support_step", lambda *args: pytest.fail("planner must not run after budget"))
    result = support_plan_node({"question": "继续", "user": user.model_dump(), "conversation_id": conversation_id, "case_id": case_id, "handoff": handoff.model_dump(), "evidence": [record.model_dump() for record in evidence], "usage": {}, "started_at": 0.0})
    assert result["status"] == "pending_human"
    assert "预算" in result["answer"]


def test_support_planner_falls_back_only_after_temporary_google_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    attempted_models: list[str] = []

    class FakeResponse:
        def __init__(self) -> None:
            self.tool_calls = [{"name": "GetOrder", "args": {"shop_id": "shop-a", "order_id": "O-1"}, "id": "call-1"}]
            self.usage_metadata = {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6}

    class FakeBoundModel:
        def __init__(self, model_name: str) -> None:
            self.model_name = model_name

        def bind_tools(self, *args, **kwargs):
            return self

        def invoke(self, *args, **kwargs):
            if len(attempted_models) == 1:
                raise RuntimeError("503 UNAVAILABLE: high demand")
            return FakeResponse()

    def fake_google_model(*, model_name: str, max_retries: int):
        attempted_models.append(model_name)
        return FakeBoundModel(model_name)

    monkeypatch.setattr("backend.app.support_agent.create_google_model", fake_google_model)
    settings = get_settings()
    handoff = SupportHandoff(
        handoff_id="handoff-1",
        conversation_id="conversation-1",
        company_id="company-a",
        customer_problem="Order did not sync",
        customer_answer="Check the sync configuration.",
        known_shop_id="shop-a",
        known_order_id="O-1",
        unresolved_reason="Needs business-state investigation",
    )
    calls, _, actual_model = plan_support_step("Investigate", handoff, [], 6)
    assert attempted_models == [settings.google_model, settings.google_fallback_model]
    assert actual_model == settings.google_fallback_model
    assert calls[0]["name"] == "GetOrder"


def test_support_planner_does_not_fallback_for_non_temporary_error(monkeypatch: pytest.MonkeyPatch) -> None:
    attempted_models: list[str] = []

    class FakeBoundModel:
        def bind_tools(self, *args, **kwargs):
            return self

        def invoke(self, *args, **kwargs):
            raise ValueError("invalid structured output")

    def fake_google_model(*, model_name: str, max_retries: int):
        attempted_models.append(model_name)
        return FakeBoundModel()

    monkeypatch.setattr("backend.app.support_agent.create_google_model", fake_google_model)
    settings = get_settings()
    handoff = SupportHandoff(
        handoff_id="handoff-1",
        conversation_id="conversation-1",
        company_id="company-a",
        customer_problem="Order did not sync",
        customer_answer="Check the sync configuration.",
        known_shop_id="shop-a",
        known_order_id="O-1",
        unresolved_reason="Needs business-state investigation",
    )
    with pytest.raises(ValueError, match="invalid structured output"):
        plan_support_step("Investigate", handoff, [], 6)
    assert attempted_models == [settings.google_model]


def test_conflicting_shipment_evidence_cannot_be_rendered_as_confirmed_root_cause() -> None:
    first_id = str(uuid4())
    second_id = str(uuid4())
    records = [
        EvidenceRecord(evidence_id=first_id, sequence=1, batch_id=str(uuid4()), parallel=True, tool_name="GetShipment", request={"shop_id": "shop-a", "order_id": "O-1"}, response={"shipment_id": "S-1", "tracking_number": "TRACK-A"}, source_service="warehouse", status="success", latency_ms=1, object_type="shipment", object_id="O-1"),
        EvidenceRecord(evidence_id=second_id, sequence=2, batch_id=str(uuid4()), parallel=True, tool_name="GetPlatformShipment", request={"shop_id": "shop-a", "order_id": "O-1"}, response={"shipment_id": "S-1", "tracking_number": "TRACK-B"}, source_service="platform", status="success", latency_ms=1, object_type="shipment", object_id="O-1"),
    ]
    answer, status = render_control_result({"name": "FinishInvestigation", "args": {"summary": "已确认", "confirmed_facts": [{"text": "平台正确", "evidence_ids": [first_id, second_id]}]}}, records)
    assert status == "pending_human"
    assert "矛盾" in answer
