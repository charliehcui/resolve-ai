from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.database import get_connection
from simulator.services import common
from simulator.services.common import OrderEvent
from simulator.services.merchant import app as merchant_app
from simulator.services.merchant import store_order_event
from simulator.services.platform import app as platform_app
from simulator.services.worker import process_next_task, recover_interrupted_tasks


@pytest.fixture()
def service_token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(common, "read_service_token", lambda name: "test-service-token")
    return "test-service-token"


def event(event_id: str | None = None, sku: str = "SKU-1", order_id: str = "O-TEST", company_id: str = "company-a", shop_id: str = "shop-a") -> OrderEvent:
    return OrderEvent(event_id=event_id or str(uuid4()), company_id=company_id, shop_id=shop_id, external_order_id=order_id, sku=sku, quantity=2, amount_minor=20000, payment_status="paid")


def test_receipt_and_task_are_committed_before_success(seeded_database: dict[str, str], service_token: str) -> None:
    order_event = event()
    response = TestClient(merchant_app).post("/events/orders", json=order_event.model_dump(), headers={"X-Service-Token": service_token})
    assert response.status_code == 200
    with get_connection() as connection:
        receipt_count = connection.execute("SELECT COUNT(*) AS count FROM merchant.order_event_receipts WHERE event_id = %s", (order_event.event_id,)).fetchone()["count"]
        task_count = connection.execute("SELECT COUNT(*) AS count FROM merchant.order_tasks WHERE event_id = %s", (order_event.event_id,)).fetchone()["count"]
    assert receipt_count == 1
    assert task_count == 1


def test_duplicate_event_creates_one_business_order(seeded_database: dict[str, str], service_token: str) -> None:
    order_event = event()
    client = TestClient(merchant_app)
    assert client.post("/events/orders", json=order_event.model_dump(), headers={"X-Service-Token": service_token}).json()["duplicate"] is False
    assert client.post("/events/orders", json=order_event.model_dump(), headers={"X-Service-Token": service_token}).json()["duplicate"] is True
    assert process_next_task()["status"] == "completed"
    with get_connection() as connection:
        order_count = connection.execute("SELECT COUNT(*) AS count FROM merchant.orders WHERE event_id = %s", (order_event.event_id,)).fetchone()["count"]
    assert order_count == 1


def test_same_event_id_with_different_content_conflicts(seeded_database: dict[str, str], service_token: str) -> None:
    order_event = event()
    client = TestClient(merchant_app)
    assert client.post("/events/orders", json=order_event.model_dump(), headers={"X-Service-Token": service_token}).status_code == 200
    changed = order_event.model_copy(update={"quantity": 3})
    assert client.post("/events/orders", json=changed.model_dump(), headers={"X-Service-Token": service_token}).status_code == 409


def test_disabled_sync_leaves_real_blocked_task_without_order(seeded_database: dict[str, str]) -> None:
    with get_connection() as connection:
        connection.execute("UPDATE merchant.shops SET sync_enabled = FALSE WHERE company_id = 'company-a' AND shop_id = 'shop-a'")
    order_event = event()
    store_order_event(order_event)
    result = process_next_task()
    assert result == {"task_id": result["task_id"], "status": "blocked", "error_code": "ORDER_SYNC_DISABLED"}
    with get_connection() as connection:
        order_count = connection.execute("SELECT COUNT(*) AS count FROM merchant.orders WHERE event_id = %s", (order_event.event_id,)).fetchone()["count"]
    assert order_count == 0


def test_missing_mapping_is_processing_failure(seeded_database: dict[str, str]) -> None:
    order_event = event(sku="UNKNOWN-SKU")
    store_order_event(order_event)
    result = process_next_task()
    assert result["status"] == "blocked"
    assert result["error_code"] == "SKU_MAPPING_MISSING"


def test_interrupted_task_continues_after_worker_restart(seeded_database: dict[str, str]) -> None:
    order_event = event()
    stored = store_order_event(order_event)
    with get_connection() as connection:
        connection.execute("UPDATE merchant.order_tasks SET status = 'processing' WHERE task_id = %s", (stored["task_id"],))
    assert recover_interrupted_tasks() == 1
    assert process_next_task()["status"] == "completed"


def test_platform_duplicate_reuses_event_and_conflicting_content_is_rejected(seeded_database: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("simulator.services.platform.deliver_event", lambda order_event: ("delivered", 200, {"accepted": True}, None))
    client = TestClient(platform_app)
    headers = {"Authorization": f"Bearer {seeded_database['token_a']}"}
    payload = {"shop_id": "shop-a", "external_order_id": "O-PLATFORM", "sku": "SKU-1", "quantity": 2, "amount_minor": 20000, "payment_status": "paid"}
    first = client.post("/orders", json=payload, headers=headers)
    second = client.post("/orders", json=payload, headers=headers)
    conflict = client.post("/orders", json={**payload, "quantity": 3}, headers=headers)
    assert first.status_code == 200
    assert second.json()["event_id"] == first.json()["event_id"]
    assert second.json()["duplicate_order"] is True
    assert conflict.status_code == 409
