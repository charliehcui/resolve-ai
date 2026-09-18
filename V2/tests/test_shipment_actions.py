import subprocess
import sys
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
import pytest
from fastapi.encoders import jsonable_encoder

from backend.app.actions import claim_action_execution, decide_action, propose_shipment_recovery
from backend.app.auth import authenticate
from backend.app.database import create_conversation, get_connection
from backend.app.handoff import handoff_to_support
from backend.app.models import AuthContext
from backend.app.support_tools import TOOL_FUNCTIONS, ToolResult
from simulator.services import common
from simulator.services.common import BusinessAuth, OrderCreate, PlatformShipmentUpdate, ShipmentCreate, ShipmentEvent, ShipmentRepairRequest, WarehouseOrderRequest
from simulator.services.merchant import receive_shipment_event, receive_shipment_repair, store_order_event
from simulator.services.platform import accept_shipment, create_order
from simulator.services.warehouse import create_shipment, receive_order
from simulator.services.worker import process_next_dispatch_task, process_next_shipment_recovery_task, process_next_shipment_task, process_next_task, reconcile_next_unknown_shipment


def shipment_tool(name: str, auth: AuthContext, shop_id: str, order_id: str | None = None) -> ToolResult:
    with get_connection() as connection:
        if name == "GetShopStatus":
            row = connection.execute("SELECT company_id, shop_id, channel, sync_enabled, shipment_sync_enabled, version FROM merchant.shops WHERE company_id = %s AND shop_id = %s", (auth.company_id, shop_id)).fetchone()
        elif name == "GetOrder":
            row = connection.execute("SELECT event_id::text, payment_status, version FROM platform.orders WHERE company_id = %s AND shop_id = %s AND external_order_id = %s", (auth.company_id, shop_id, order_id)).fetchone()
        elif name == "GetShipment":
            row = connection.execute("""SELECT o.warehouse_order_id::text, o.status AS warehouse_order_status, s.shipment_id::text, s.carrier, s.tracking_number,
                s.version AS shipment_version, s.shipped_at, (SELECT COUNT(*) FROM warehouse.shipments c WHERE c.warehouse_order_id = o.warehouse_order_id) AS shipment_count
                FROM warehouse.orders o LEFT JOIN warehouse.shipments s ON s.warehouse_order_id = o.warehouse_order_id
                WHERE o.company_id = %s AND o.shop_id = %s AND o.external_order_id = %s""", (auth.company_id, shop_id, order_id)).fetchone()
        elif name == "GetShipmentRecords":
            row = connection.execute("""SELECT r.shipment_id::text, t.status AS task_status, t.error_code, s.carrier, s.tracking_number, s.version
                FROM merchant.shipment_event_receipts r JOIN merchant.shipment_tasks t ON t.shipment_id = r.shipment_id
                LEFT JOIN merchant.shipments s ON s.shipment_id = r.shipment_id
                WHERE r.company_id = %s AND r.shop_id = %s AND r.external_order_id = %s""", (auth.company_id, shop_id, order_id)).fetchone()
        else:
            row = connection.execute("SELECT shipment_id::text, carrier, tracking_number, status, version FROM platform.shipments WHERE company_id = %s AND shop_id = %s AND external_order_id = %s", (auth.company_id, shop_id, order_id)).fetchone()
    request = {"shop_id": shop_id, **({"order_id": order_id} if order_id else {})}
    return ToolResult(tool_name=name, request=request, response=dict(row) if row else {}, source_service={"GetOrder": "platform", "GetShipment": "warehouse", "GetShipmentRecords": "merchant", "GetPlatformShipment": "platform", "GetShopStatus": "merchant"}[name], source_record_id=str(row.get("shipment_id") or row.get("event_id") or shop_id) if row else None, status="success" if row else "not_found", latency_ms=1)


@pytest.fixture()
def shipment_action_runtime(seeded_database: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> tuple[AuthContext, str]:
    monkeypatch.setattr(common, "read_service_token", lambda name: "test-service-token")
    monkeypatch.setattr("simulator.services.worker.read_service_token", lambda name: "test-service-token")
    monkeypatch.setattr("simulator.services.platform.deliver_event", lambda event: ("delivered", 200, store_order_event(event), None))

    def warehouse_post(url: str, json: dict[str, object], **kwargs: object) -> httpx.Response:
        return httpx.Response(200, json=jsonable_encoder(receive_order(WarehouseOrderRequest(**json), "test-service-token")))

    monkeypatch.setattr("simulator.services.worker.httpx.post", warehouse_post)
    business_auth = BusinessAuth(company_id="company-a", user_id="admin-a", role="admin")
    create_order(OrderCreate(shop_id="shop-a", external_order_id="O-RECOVER-SHIP", sku="SKU-1", quantity=1, amount_minor=1000, payment_status="paid"), business_auth)
    process_next_task()
    process_next_dispatch_task()
    with get_connection() as connection:
        connection.execute("UPDATE merchant.shops SET shipment_sync_enabled = FALSE, version = version + 1 WHERE company_id = 'company-a' AND shop_id = 'shop-a'")
    monkeypatch.setattr("simulator.services.warehouse.deliver_shipment", lambda event: ("delivered", 200, receive_shipment_event(ShipmentEvent(**event.model_dump()), "test-service-token"), None))
    create_shipment(ShipmentCreate(shop_id="shop-a", external_order_id="O-RECOVER-SHIP", carrier="test-express", tracking_number="RECOVER-1"), business_auth)
    assert process_next_shipment_task()["status"] == "blocked"

    auth = authenticate(seeded_database["token_a"])
    conversation_id = create_conversation(auth.company_id, auth.user_id)
    _, case_id = handoff_to_support(auth, conversation_id, "shop-a 的订单 O-RECOVER-SHIP 仓库已发货但平台没更新", "需要调查。", [])
    for name in ("GetOrder", "GetShipment", "GetShipmentRecords", "GetPlatformShipment"):
        monkeypatch.setitem(TOOL_FUNCTIONS, name, lambda auth, shop_id, order_id, tool_name=name: shipment_tool(tool_name, auth, shop_id, order_id))
    monkeypatch.setitem(TOOL_FUNCTIONS, "GetShopStatus", lambda auth, shop_id: shipment_tool("GetShopStatus", auth, shop_id))
    monkeypatch.setattr("backend.app.actions.get_shipment", lambda auth, shop_id, order_id: shipment_tool("GetShipment", auth, shop_id, order_id))
    monkeypatch.setattr("backend.app.actions.get_shipment_records", lambda auth, shop_id, order_id: shipment_tool("GetShipmentRecords", auth, shop_id, order_id))
    monkeypatch.setattr("backend.app.actions.get_platform_shipment", lambda auth, shop_id, order_id: shipment_tool("GetPlatformShipment", auth, shop_id, order_id))
    monkeypatch.setattr("backend.app.actions.get_shop_status", lambda auth, shop_id: shipment_tool("GetShopStatus", auth, shop_id))
    monkeypatch.setattr("backend.app.actions.get_order", lambda auth, shop_id, order_id: shipment_tool("GetOrder", auth, shop_id, order_id))
    monkeypatch.setattr("backend.app.actions.submit_shipment_repair", lambda payload: receive_shipment_repair(ShipmentRepairRequest(**payload), "test-service-token"))

    def platform_post(url: str, json: dict[str, object], **kwargs: object) -> httpx.Response:
        return httpx.Response(200, json=jsonable_encoder(accept_shipment(PlatformShipmentUpdate(**json), "test-service-token")))

    monkeypatch.setattr("simulator.services.worker.httpx.post", platform_post)

    def process_then_verify(auth: AuthContext, action_id: str, timeout_seconds: float = 15) -> dict[str, object]:
        assert process_next_shipment_recovery_task()["status"] == "completed"
        from backend.app.verification import verify_shipment_recovery

        return verify_shipment_recovery(auth, action_id)

    monkeypatch.setattr("backend.app.verification.wait_for_shipment_verification", process_then_verify)
    return auth, case_id


def test_approved_shipment_recovery_updates_platform_without_second_warehouse_shipment(shipment_action_runtime: tuple[AuthContext, str]) -> None:
    auth, case_id = shipment_action_runtime
    proposed = propose_shipment_recovery(auth, case_id, enable_shipment_sync=True)
    completed = decide_action(auth, proposed["action_id"], "approve")
    assert completed["status"] == "verified_resolved"
    with get_connection() as connection:
        warehouse_count = connection.execute("SELECT COUNT(*) AS count FROM warehouse.shipments WHERE external_order_id = 'O-RECOVER-SHIP'").fetchone()["count"]
        platform_count = connection.execute("SELECT COUNT(*) AS count FROM platform.shipments WHERE external_order_id = 'O-RECOVER-SHIP'").fetchone()["count"]
        repair_count = connection.execute("SELECT COUNT(*) AS count FROM merchant.shipment_repair_receipts WHERE action_id = %s", (proposed["action_id"],)).fetchone()["count"]
    assert (warehouse_count, platform_count, repair_count) == (1, 1, 1)


def test_unapproved_shipment_proposal_performs_no_write(shipment_action_runtime: tuple[AuthContext, str]) -> None:
    auth, case_id = shipment_action_runtime
    proposed = propose_shipment_recovery(auth, case_id, enable_shipment_sync=True)
    with get_connection() as connection:
        receipt_count = connection.execute("SELECT COUNT(*) AS count FROM merchant.shipment_repair_receipts WHERE action_id = %s", (proposed["action_id"],)).fetchone()["count"]
        platform_count = connection.execute("SELECT COUNT(*) AS count FROM platform.shipments WHERE external_order_id = 'O-RECOVER-SHIP'").fetchone()["count"]
    assert receipt_count == 0
    assert platform_count == 0


def test_response_lost_after_platform_commit_reconciles_without_second_shipment(shipment_action_runtime: tuple[AuthContext, str], monkeypatch: pytest.MonkeyPatch) -> None:
    auth, case_id = shipment_action_runtime
    proposed = propose_shipment_recovery(auth, case_id, enable_shipment_sync=True)

    def commit_then_lose_response(url: str, json: dict[str, object], **kwargs: object) -> httpx.Response:
        accept_shipment(PlatformShipmentUpdate(**json), "test-service-token")
        raise httpx.ReadTimeout("response lost", request=httpx.Request("POST", url))

    def read_platform(url: str, params: dict[str, object], **kwargs: object) -> httpx.Response:
        with get_connection() as connection:
            row = connection.execute("SELECT shipment_id::text, carrier, tracking_number, status, version FROM platform.shipments WHERE company_id = 'company-a' AND shop_id = %s AND external_order_id = %s", (params["shop_id"], "O-RECOVER-SHIP")).fetchone()
        return httpx.Response(200 if row else 404, json=jsonable_encoder(dict(row)) if row else {"detail": "not found"})

    monkeypatch.setattr("simulator.services.worker.httpx.post", commit_then_lose_response)
    monkeypatch.setattr("simulator.services.worker.httpx.get", read_platform)

    def recover_then_verify(auth: AuthContext, action_id: str, timeout_seconds: float = 15) -> dict[str, object]:
        assert process_next_shipment_recovery_task()["status"] == "unknown"
        assert reconcile_next_unknown_shipment()["status"] == "completed"
        from backend.app.verification import verify_shipment_recovery

        return verify_shipment_recovery(auth, action_id)

    monkeypatch.setattr("backend.app.verification.wait_for_shipment_verification", recover_then_verify)
    completed = decide_action(auth, proposed["action_id"], "approve")
    assert completed["status"] == "verified_resolved"
    with get_connection() as connection:
        platform_count = connection.execute("SELECT COUNT(*) AS count FROM platform.shipments WHERE external_order_id = 'O-RECOVER-SHIP'").fetchone()["count"]
        warehouse_count = connection.execute("SELECT COUNT(*) AS count FROM warehouse.shipments WHERE external_order_id = 'O-RECOVER-SHIP'").fetchone()["count"]
    assert (platform_count, warehouse_count) == (1, 1)


def test_changed_order_after_approval_blocks_shipment_recovery(shipment_action_runtime: tuple[AuthContext, str]) -> None:
    auth, case_id = shipment_action_runtime
    proposed = propose_shipment_recovery(auth, case_id, enable_shipment_sync=True)
    with get_connection() as connection:
        connection.execute("UPDATE platform.orders SET payment_status = 'cancelled', version = version + 1 WHERE company_id = 'company-a' AND external_order_id = 'O-RECOVER-SHIP'")
    blocked = decide_action(auth, proposed["action_id"], "approve")
    assert blocked["status"] == "blocked"
    with get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) AS count FROM merchant.shipment_repair_receipts WHERE action_id = %s", (proposed["action_id"],)).fetchone()["count"] == 0


def test_terminated_execution_process_releases_work_only_after_lease_expiry(shipment_action_runtime: tuple[AuthContext, str]) -> None:
    auth, case_id = shipment_action_runtime
    proposed = propose_shipment_recovery(auth, case_id, enable_shipment_sync=True)
    action_id = proposed["action_id"]
    request_id = str(uuid5(NAMESPACE_URL, proposed["idempotency_key"]))
    with get_connection() as connection:
        connection.execute("INSERT INTO support.action_decisions (decision_id, action_id, decision, decided_by) VALUES (%s, %s, 'approved', %s)", (str(uuid4()), action_id, auth.user_id))
        connection.execute("UPDATE support.action_proposals SET status = 'approved' WHERE action_id = %s", (action_id,))
    code = f"from backend.app.actions import claim_action_execution; import time; print(claim_action_execution('{action_id}', '{request_id}'), flush=True); time.sleep(60)"
    child = subprocess.Popen([sys.executable, "-c", code], cwd=str(__import__("pathlib").Path(__file__).parents[1]), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert child.stdout is not None
    assert child.stdout.readline().strip() == "acquired"
    child.terminate()
    child.wait(timeout=5)
    assert claim_action_execution(action_id, request_id) == "busy"
    with get_connection() as connection:
        connection.execute("UPDATE support.action_executions SET claim_until = %s WHERE action_id = %s", (datetime.now(UTC) - timedelta(seconds=1), action_id))
    assert claim_action_execution(action_id, request_id) == "acquired"
    with get_connection() as connection:
        execution = connection.execute("SELECT attempts, request_id::text FROM support.action_executions WHERE action_id = %s", (action_id,)).fetchone()
        warehouse_count = connection.execute("SELECT COUNT(*) AS count FROM warehouse.shipments WHERE external_order_id = 'O-RECOVER-SHIP'").fetchone()["count"]
    assert execution["attempts"] == 2
    assert execution["request_id"] == request_id
    assert warehouse_count == 1
