import httpx
import pytest
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient

from backend.app.database import get_connection
from simulator.services import common
from simulator.services.common import BusinessAuth, OrderCreate, PlatformShipmentUpdate, ShipmentCreate, ShipmentEvent, WarehouseOrderRequest
from simulator.services.merchant import app as merchant_app
from simulator.services.merchant import receive_shipment_event, store_order_event
from simulator.services.platform import accept_shipment, create_order
from simulator.services.platform import app as platform_app
from simulator.services.warehouse import app as warehouse_app
from simulator.services.warehouse import create_shipment, receive_order
from simulator.services.worker import process_next_dispatch_task, process_next_shipment_task, process_next_task


@pytest.fixture()
def shipment_runtime(seeded_database: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> BusinessAuth:
    monkeypatch.setattr(common, "read_service_token", lambda name: "test-service-token")
    monkeypatch.setattr("simulator.services.worker.read_service_token", lambda name: "test-service-token")
    monkeypatch.setattr("simulator.services.platform.deliver_event", lambda event: ("delivered", 200, store_order_event(event), None))

    def warehouse_post(url: str, json: dict[str, object], **kwargs: object) -> httpx.Response:
        result = receive_order(WarehouseOrderRequest(**json), "test-service-token")
        return httpx.Response(200, json=result)

    monkeypatch.setattr("simulator.services.worker.httpx.post", warehouse_post)
    return BusinessAuth(company_id="company-a", user_id="admin-a", role="admin")


def create_dispatched_order(user: BusinessAuth, order_id: str = "O-SHIP") -> str:
    result = create_order(OrderCreate(shop_id="shop-a", external_order_id=order_id, sku="SKU-1", quantity=2, amount_minor=20000, payment_status="paid"), user)
    assert process_next_task()["status"] == "completed"
    assert process_next_dispatch_task()["status"] == "completed"
    return str(result["event_id"])


def deliver_to_merchant(event: ShipmentEvent) -> tuple[str, int, dict[str, object], None]:
    result = receive_shipment_event(event, "test-service-token")
    return "delivered", 200, result, None


def platform_post(url: str, json: dict[str, object], **kwargs: object) -> httpx.Response:
    result = accept_shipment(PlatformShipmentUpdate(**json), "test-service-token")
    return httpx.Response(200, json=jsonable_encoder(result))


def test_real_shipment_flow_has_independent_warehouse_merchant_and_platform_facts(shipment_runtime: BusinessAuth, monkeypatch: pytest.MonkeyPatch) -> None:
    create_dispatched_order(shipment_runtime)
    monkeypatch.setattr("simulator.services.warehouse.deliver_shipment", deliver_to_merchant)
    monkeypatch.setattr("simulator.services.worker.httpx.post", platform_post)
    result = create_shipment(ShipmentCreate(shop_id="shop-a", external_order_id="O-SHIP", carrier="test-express", tracking_number="TEST-1001"), shipment_runtime)
    assert result["warehouse_status"] == "shipped"
    assert process_next_shipment_task()["status"] == "completed"
    with get_connection() as connection:
        warehouse = connection.execute("SELECT tracking_number FROM warehouse.shipments WHERE company_id = 'company-a' AND external_order_id = 'O-SHIP'").fetchone()
        merchant = connection.execute("SELECT tracking_number FROM merchant.shipments WHERE company_id = 'company-a' AND external_order_id = 'O-SHIP'").fetchone()
        platform = connection.execute("SELECT tracking_number FROM platform.shipments WHERE company_id = 'company-a' AND external_order_id = 'O-SHIP'").fetchone()
        shipment_count = connection.execute("SELECT COUNT(*) AS count FROM warehouse.shipments WHERE company_id = 'company-a' AND external_order_id = 'O-SHIP'").fetchone()["count"]
    assert warehouse["tracking_number"] == merchant["tracking_number"] == platform["tracking_number"] == "TEST-1001"
    assert shipment_count == 1


def test_duplicate_dispatch_and_shipment_do_not_create_a_second_business_effect(shipment_runtime: BusinessAuth, monkeypatch: pytest.MonkeyPatch) -> None:
    create_dispatched_order(shipment_runtime, "O-DUP-SHIP")
    with get_connection() as connection:
        merchant_order_id = connection.execute("SELECT merchant_order_id::text FROM merchant.orders WHERE company_id = 'company-a' AND external_order_id = 'O-DUP-SHIP'").fetchone()["merchant_order_id"]
    duplicate_order = receive_order(WarehouseOrderRequest(merchant_order_id=merchant_order_id, company_id="company-a", shop_id="shop-a", external_order_id="O-DUP-SHIP", merchant_sku="MERCHANT-SKU-1", quantity=2), "test-service-token")
    assert duplicate_order["duplicate"] is True
    monkeypatch.setattr("simulator.services.warehouse.deliver_shipment", deliver_to_merchant)
    payload = ShipmentCreate(shop_id="shop-a", external_order_id="O-DUP-SHIP", carrier="test-express", tracking_number="TEST-DUP")
    first = create_shipment(payload, shipment_runtime)
    second = create_shipment(payload, shipment_runtime)
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    with pytest.raises(Exception, match="already shipped"):
        create_shipment(payload.model_copy(update={"tracking_number": "CHANGED"}), shipment_runtime)
    with get_connection() as connection:
        count = connection.execute("SELECT COUNT(*) AS count FROM warehouse.shipments WHERE company_id = 'company-a' AND external_order_id = 'O-DUP-SHIP'").fetchone()["count"]
    assert count == 1


def test_disabled_shipment_sync_preserves_real_upstream_facts_without_platform_success(shipment_runtime: BusinessAuth, monkeypatch: pytest.MonkeyPatch) -> None:
    create_dispatched_order(shipment_runtime, "O-SYNC-OFF")
    with get_connection() as connection:
        connection.execute("UPDATE merchant.shops SET shipment_sync_enabled = FALSE WHERE company_id = 'company-a' AND shop_id = 'shop-a'")
    monkeypatch.setattr("simulator.services.warehouse.deliver_shipment", deliver_to_merchant)
    create_shipment(ShipmentCreate(shop_id="shop-a", external_order_id="O-SYNC-OFF", carrier="test-express", tracking_number="TEST-OFF"), shipment_runtime)
    result = process_next_shipment_task()
    assert result["status"] == "blocked"
    assert result["error_code"] == "SHIPMENT_SYNC_DISABLED"
    with get_connection() as connection:
        warehouse_count = connection.execute("SELECT COUNT(*) AS count FROM warehouse.shipments WHERE external_order_id = 'O-SYNC-OFF'").fetchone()["count"]
        merchant_count = connection.execute("SELECT COUNT(*) AS count FROM merchant.shipments WHERE external_order_id = 'O-SYNC-OFF'").fetchone()["count"]
        platform_count = connection.execute("SELECT COUNT(*) AS count FROM platform.shipments WHERE external_order_id = 'O-SYNC-OFF'").fetchone()["count"]
    assert (warehouse_count, merchant_count, platform_count) == (1, 1, 0)


def test_missing_warehouse_order_cannot_be_shipped(shipment_runtime: BusinessAuth) -> None:
    with pytest.raises(Exception, match="has not been received"):
        create_shipment(ShipmentCreate(shop_id="shop-a", external_order_id="O-NOT-THERE", carrier="test-express", tracking_number="NONE"), shipment_runtime)


def test_shipment_internal_endpoints_do_not_leak_across_companies(shipment_runtime: BusinessAuth, monkeypatch: pytest.MonkeyPatch) -> None:
    create_dispatched_order(shipment_runtime, "O-SHIP-SCOPE")
    monkeypatch.setattr("simulator.services.warehouse.deliver_shipment", deliver_to_merchant)
    monkeypatch.setattr("simulator.services.worker.httpx.post", platform_post)
    create_shipment(ShipmentCreate(shop_id="shop-a", external_order_id="O-SHIP-SCOPE", carrier="test-express", tracking_number="SCOPE-1"), shipment_runtime)
    assert process_next_shipment_task()["status"] == "completed"
    headers_a = {"X-Service-Token": "test-service-token", "X-Company-ID": "company-a"}
    headers_b = {"X-Service-Token": "test-service-token", "X-Company-ID": "company-b"}
    warehouse_client = TestClient(warehouse_app)
    merchant_client = TestClient(merchant_app)
    platform_client = TestClient(platform_app)
    assert warehouse_client.get("/internal/shipments/O-SHIP-SCOPE", params={"shop_id": "shop-a"}, headers=headers_a).status_code == 200
    assert platform_client.get("/internal/shipments/O-SHIP-SCOPE", params={"shop_id": "shop-a"}, headers=headers_a).status_code == 200
    assert merchant_client.get("/internal/shipment-records/O-SHIP-SCOPE", params={"shop_id": "shop-a"}, headers=headers_a).json()["empty"] is False
    assert warehouse_client.get("/internal/shipments/O-SHIP-SCOPE", params={"shop_id": "shop-a"}, headers=headers_b).status_code == 404
    assert platform_client.get("/internal/shipments/O-SHIP-SCOPE", params={"shop_id": "shop-a"}, headers=headers_b).status_code == 404
    other_company_merchant = merchant_client.get("/internal/shipment-records/O-SHIP-SCOPE", params={"shop_id": "shop-a"}, headers=headers_b)
    assert other_company_merchant.status_code == 200
    assert other_company_merchant.json()["empty"] is True
