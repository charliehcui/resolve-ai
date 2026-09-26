import time
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.database import get_connection
from backend.app.handoff import SupportHandoffRecord
from backend.app.support_agent import SupportNextStep
from backend.app.support_tools import TOOL_FUNCTIONS, request_fact
from backend.app.support_workflow import support_agent_node
from simulator.services import common
from simulator.services.common import OrderEvent
from simulator.services.merchant import app as merchant_app
from simulator.services.merchant import store_order_event
from simulator.services.worker import process_next_task


@pytest.mark.parametrize(
    ("connection_status", "task_status", "http_status", "error_code"),
    [
        ("auth_expired", "blocked", 401, "CHANNEL_AUTH_EXPIRED"),
        ("forbidden", "blocked", 403, "CHANNEL_FORBIDDEN"),
        ("rate_limited", "failed", 429, "CHANNEL_RATE_LIMITED"),
        ("internal_error", "failed", 500, "CHANNEL_INTERNAL_ERROR"),
        ("unavailable", "failed", 503, "CHANNEL_UNAVAILABLE"),
        ("timeout", "failed", None, "CHANNEL_TIMEOUT"),
    ],
)
def test_channel_failures_are_recorded_from_shop_state_without_creating_order(seeded_database: dict[str, str], connection_status: str, task_status: str, http_status: int | None, error_code: str) -> None:
    with get_connection() as connection:
        connection.execute("UPDATE merchant.shops SET connection_status = %s WHERE company_id = 'company-a' AND shop_id = 'shop-a'", (connection_status,))
    event = OrderEvent(event_id=str(uuid4()), company_id="company-a", shop_id="shop-a", external_order_id=f"O-{connection_status}", sku="SKU-1", quantity=1, amount_minor=1000, payment_status="paid")
    store_order_event(event)
    result = process_next_task()
    assert result and result["status"] == task_status and result["error_code"] == error_code
    assert result["http_status"] == http_status and result["request_id"]
    with get_connection() as connection:
        order_count = connection.execute("SELECT COUNT(*) AS count FROM merchant.orders WHERE event_id = %s", (event.event_id,)).fetchone()["count"]
        failure = connection.execute("SELECT company_id, shop_id, http_status, error_code, request_id::text FROM merchant.channel_failures WHERE request_id = %s", (result["request_id"],)).fetchone()
    assert order_count == 0
    assert failure["company_id"] == "company-a" and failure["shop_id"] == "shop-a"
    assert failure["http_status"] == http_status and failure["error_code"] == error_code


def test_failed_channel_does_not_affect_other_shop_or_company(seeded_database: dict[str, str]) -> None:
    with get_connection() as connection:
        connection.execute("UPDATE merchant.shops SET connection_status = 'internal_error' WHERE company_id = 'company-a' AND shop_id = 'shop-a'")
    events = [
        OrderEvent(event_id=str(uuid4()), company_id="company-a", shop_id="shop-a", external_order_id="O-FAIL-A", sku="SKU-1", quantity=1, amount_minor=1000, payment_status="paid"),
        OrderEvent(event_id=str(uuid4()), company_id="company-a", shop_id="shop-b", external_order_id="O-OK-B", sku="SKU-1", quantity=1, amount_minor=1000, payment_status="paid"),
        OrderEvent(event_id=str(uuid4()), company_id="company-b", shop_id="shop-b-company", external_order_id="O-OK-COMPANY-B", sku="SKU-1", quantity=1, amount_minor=1000, payment_status="paid"),
    ]
    for event in events:
        store_order_event(event)
    assert [process_next_task()["status"] for _ in events] == ["failed", "completed", "completed"]
    with get_connection() as connection:
        rows = connection.execute("SELECT company_id, shop_id, external_order_id FROM merchant.orders ORDER BY external_order_id").fetchall()
    assert {(row["company_id"], row["shop_id"], row["external_order_id"]) for row in rows} == {("company-a", "shop-b", "O-OK-B"), ("company-b", "shop-b-company", "O-OK-COMPANY-B")}


def test_connection_restore_requires_lab_operator_token(seeded_database: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "read_service_token", lambda name: "lab-test-token")
    client = TestClient(merchant_app)
    with get_connection() as connection:
        connection.execute("UPDATE merchant.shops SET connection_status = 'auth_expired' WHERE company_id = 'company-a' AND shop_id = 'shop-a'")
    assert client.post("/lab/shops/shop-a/connection/restore", headers={"Authorization": f"Bearer {seeded_database['token_a']}"}).status_code == 401
    restored = client.post("/lab/shops/shop-a/connection/restore", headers={"X-Lab-Token": "lab-test-token"})
    assert restored.status_code == 200 and restored.json()["connection_status"] == "authorized"
    assert all("restore" not in name.lower() and "connection" not in name.lower() or name == "CheckConnection" for name in TOOL_FUNCTIONS)


@pytest.mark.parametrize(
    ("status_code", "status", "error_code"),
    [(401, "forbidden", "AUTHENTICATION_REQUIRED"), (403, "forbidden", "FORBIDDEN"), (429, "error", "RATE_LIMITED"), (500, "error", "INTERNAL_ERROR"), (503, "error", "SERVICE_UNAVAILABLE")],
)
def test_tool_http_errors_keep_status_and_request_id(monkeypatch: pytest.MonkeyPatch, status_code: int, status: str, error_code: str) -> None:
    monkeypatch.setattr("backend.app.support_tools.read_service_token", lambda name: "test")
    request = httpx.Request("GET", "http://service/fact")
    response = httpx.Response(status_code, json={"detail": "controlled failure"}, request=request)
    monkeypatch.setattr("backend.app.support_tools.httpx.get", lambda *args, **kwargs: response)
    result = request_fact("GetOrder", "platform", "http://service/fact", "company-a", {})
    assert result.status == status
    assert result.response["http_status"] == status_code
    assert result.response["error_code"] == error_code
    assert result.response["request_id"]


def test_tool_timeout_is_not_reported_as_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.app.support_tools.read_service_token", lambda name: "test")
    monkeypatch.setattr("backend.app.support_tools.httpx.get", lambda *args, **kwargs: (_ for _ in ()).throw(httpx.ReadTimeout("late")))
    result = request_fact("GetOrder", "platform", "http://service/fact", "company-a", {})
    assert result.status == "unavailable"
    assert result.response["error_code"] == "TIMEOUT"
    assert result.response["request_id"]


def test_error_budget_stops_before_another_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    handoff = SupportHandoffRecord(handoff_id="h", conversation_id="c", company_id="company-a", customer_problem="x", known_shop_id="shop-a", known_order_id="O-1")
    evidence = []
    for index in range(3):
        evidence.append({"evidence_id": str(uuid4()), "sequence": index + 1, "batch_id": str(uuid4()), "parallel": False, "tool_name": "GetOrder", "request": {}, "response": {}, "source_service": "platform", "status": "error", "latency_ms": 1})
    monkeypatch.setattr("backend.app.support_workflow.decide_support_next_step", lambda *args: pytest.fail("planner must not run after error budget"))
    result = support_agent_node({"handoff": handoff.model_dump(), "evidence": evidence, "started_at": time.perf_counter()})
    support_decision = SupportNextStep.model_validate(result["support_decision"])
    assert support_decision.next_step == "human_support"


def test_support_tool_list_has_no_connection_or_stock_write_capability() -> None:
    assert set(TOOL_FUNCTIONS).isdisjoint({"RestoreConnection", "SetConnection", "SetStock", "PublishStock", "AdjustStock"})
