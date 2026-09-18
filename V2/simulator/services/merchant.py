import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Annotated
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from langsmith import traceable

from backend.app.database import get_connection
from backend.app.trace import current_trace_id
from simulator.services.common import (
    BusinessAuth,
    ConnectionUpdate,
    OrderEvent,
    OrderRepairRequest,
    ShipmentEvent,
    ShipmentRepairRequest,
    ShopSyncUpdate,
    StockPublishRequest,
    current_user,
    payload_hash,
    read_service_token,
    require_token,
)

app = FastAPI(title="ResolveAI Merchant Simulator")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/events/orders")
def receive_order_event(event: OrderEvent, x_service_token: str | None = Header(default=None)) -> dict[str, object]:
    require_token(x_service_token, "merchant-ingest")
    return store_order_event(event)


@traceable(name="merchant_store_order_event", run_type="tool")
def store_order_event(event: OrderEvent) -> dict[str, object]:
    content_hash = payload_hash(event)
    trace_id = current_trace_id()
    with get_connection() as connection:
        shop = connection.execute("SELECT 1 FROM merchant.shops WHERE company_id = %s AND shop_id = %s", (event.company_id, event.shop_id)).fetchone()
        if shop is None:
            raise HTTPException(status_code=404, detail="Shop not found")
        existing = connection.execute("SELECT payload_hash FROM merchant.order_event_receipts WHERE event_id = %s", (event.event_id,)).fetchone()
        if existing:
            if existing["payload_hash"] != content_hash:
                raise HTTPException(status_code=409, detail="Event identifier already exists with different content")
            task = connection.execute("SELECT task_id::text, status FROM merchant.order_tasks WHERE event_id = %s", (event.event_id,)).fetchone()
            return {"accepted": True, "duplicate": True, "task_id": task["task_id"], "task_status": task["status"]}
        task_id = str(uuid4())
        connection.execute(
            "INSERT INTO merchant.order_event_receipts (event_id, company_id, shop_id, external_order_id, payload_hash, payload, trace_id) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)",
            (event.event_id, event.company_id, event.shop_id, event.external_order_id, content_hash, json.dumps(event.model_dump()), trace_id),
        )
        connection.execute(
            "INSERT INTO merchant.order_tasks (task_id, event_id, company_id, shop_id, external_order_id) VALUES (%s, %s, %s, %s, %s)",
            (task_id, event.event_id, event.company_id, event.shop_id, event.external_order_id),
        )
    return {"accepted": True, "duplicate": False, "task_id": task_id, "task_status": "pending"}


@app.get("/orders/{external_order_id}")
def get_order(external_order_id: str, shop_id: str, auth: Annotated[BusinessAuth, Depends(current_user)]) -> dict[str, object]:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT merchant_order_id::text, event_id::text, company_id, shop_id, external_order_id, merchant_sku, quantity, amount_minor, created_at FROM merchant.orders WHERE company_id = %s AND shop_id = %s AND external_order_id = %s",
            (auth.company_id, shop_id, external_order_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Merchant order not found")
    return dict(row)


@app.get("/tasks/{event_id}")
def get_task(event_id: str, auth: Annotated[BusinessAuth, Depends(current_user)]) -> dict[str, object]:
    with get_connection() as connection:
        row = connection.execute("SELECT task_id::text, event_id::text, company_id, shop_id, external_order_id, status, error_code, http_status, failure_request_id::text, attempts, updated_at FROM merchant.order_tasks WHERE event_id = %s AND company_id = %s", (event_id, auth.company_id)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return dict(row)


@app.post("/lab/shops/{shop_id}/sync")
def set_shop_sync(shop_id: str, update: ShopSyncUpdate, company_id: str = "company-a", x_lab_token: str | None = Header(default=None)) -> dict[str, object]:
    require_token(x_lab_token, "lab-control")
    with get_connection() as connection:
        row = connection.execute("UPDATE merchant.shops SET sync_enabled = %s, version = version + 1 WHERE company_id = %s AND shop_id = %s RETURNING company_id, shop_id, sync_enabled, version", (update.enabled, company_id, shop_id)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Shop not found")
    return dict(row)


@app.post("/lab/shops/{shop_id}/connection")
def set_connection(shop_id: str, update: ConnectionUpdate, company_id: str = "company-a", x_lab_token: str | None = Header(default=None)) -> dict[str, object]:
    require_token(x_lab_token, "lab-control")
    with get_connection() as connection:
        row = connection.execute("UPDATE merchant.shops SET connection_status = %s, version = version + 1 WHERE company_id = %s AND shop_id = %s RETURNING company_id, shop_id, channel, connection_status, version", (update.status, company_id, shop_id)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Shop not found")
    return dict(row)


@app.post("/lab/shops/{shop_id}/connection/restore")
def restore_connection(shop_id: str, company_id: str = "company-a", x_lab_token: str | None = Header(default=None)) -> dict[str, object]:
    require_token(x_lab_token, "lab-control")
    return set_connection(shop_id, ConnectionUpdate(status="authorized"), company_id, x_lab_token)


@app.get("/internal/shops/{shop_id}/status")
def internal_shop_status(shop_id: str, x_company_id: Annotated[str, Header()], x_service_token: Annotated[str | None, Header()] = None) -> dict[str, object]:
    require_token(x_service_token, "support-read")
    with get_connection() as connection:
        row = connection.execute("SELECT company_id, shop_id, channel, sync_enabled, shipment_sync_enabled, version FROM merchant.shops WHERE company_id = %s AND shop_id = %s", (x_company_id, shop_id)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Shop not found")
    return dict(row)


@app.get("/internal/shops/{shop_id}/connection")
def internal_connection_status(shop_id: str, x_company_id: Annotated[str, Header()], x_service_token: Annotated[str | None, Header()] = None) -> dict[str, object]:
    require_token(x_service_token, "support-read")
    with get_connection() as connection:
        row = connection.execute("SELECT company_id, shop_id, channel, connection_status, version FROM merchant.shops WHERE company_id = %s AND shop_id = %s", (x_company_id, shop_id)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Shop not found")
    return dict(row)


@app.post("/stocks/publish")
def queue_stock_publish(request: StockPublishRequest, auth: Annotated[BusinessAuth, Depends(current_user)]) -> dict[str, object]:
    with get_connection() as connection:
        rule = connection.execute("SELECT warehouse_sku, safety_stock FROM merchant.stock_rules WHERE company_id = %s AND shop_id = %s AND platform_sku = %s AND active", (auth.company_id, request.shop_id, request.platform_sku)).fetchone()
    if rule is None:
        raise HTTPException(status_code=409, detail="Active stock mapping not found")
    warehouse_url = os.getenv("WAREHOUSE_URL", "http://127.0.0.1:8003")
    response = httpx.get(f"{warehouse_url}/internal/stocks/{rule['warehouse_sku']}", headers={"X-Service-Token": read_service_token("support-read"), "X-Company-ID": auth.company_id}, timeout=5)
    if response.status_code == 404:
        raise HTTPException(status_code=409, detail="Warehouse stock not found")
    response.raise_for_status()
    stock = response.json()
    expected = max(int(stock["physical_quantity"]) - int(stock["reserved_quantity"]) - int(rule["safety_stock"]), 0)
    request_id = str(uuid5(NAMESPACE_URL, f"stock:{auth.company_id}:{request.shop_id}:{request.platform_sku}:{stock['version']}"))
    with get_connection() as connection:
        existing = connection.execute("SELECT task_id::text, status, expected_quantity FROM merchant.stock_publish_tasks WHERE request_id = %s", (request_id,)).fetchone()
        if existing:
            return {"duplicate": True, **dict(existing)}
        task_id = str(uuid4())
        connection.execute("""INSERT INTO merchant.stock_publish_tasks (task_id, request_id, company_id, shop_id, platform_sku, warehouse_sku, warehouse_version, physical_quantity, reserved_quantity, safety_stock, expected_quantity)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""", (task_id, request_id, auth.company_id, request.shop_id, request.platform_sku, rule["warehouse_sku"], stock["version"], stock["physical_quantity"], stock["reserved_quantity"], rule["safety_stock"], expected))
    return {"duplicate": False, "task_id": task_id, "status": "pending", "expected_quantity": expected, "warehouse_version": stock["version"]}


@app.get("/internal/stock-records/{platform_sku}")
def internal_stock_records(platform_sku: str, shop_id: str, x_company_id: Annotated[str, Header()], x_service_token: Annotated[str | None, Header()] = None) -> dict[str, object]:
    require_token(x_service_token, "support-read")
    with get_connection() as connection:
        rule = connection.execute("SELECT company_id, shop_id, platform_sku, warehouse_sku, safety_stock, version AS rule_version, updated_at AS rule_updated_at FROM merchant.stock_rules WHERE company_id = %s AND shop_id = %s AND platform_sku = %s AND active", (x_company_id, shop_id, platform_sku)).fetchone()
        task = connection.execute("SELECT task_id::text, request_id::text, warehouse_version, expected_quantity, status AS task_status, http_status, error_code, failure_request_id::text, created_at, updated_at FROM merchant.stock_publish_tasks WHERE company_id = %s AND shop_id = %s AND platform_sku = %s ORDER BY created_at DESC LIMIT 1", (x_company_id, shop_id, platform_sku)).fetchone()
    if rule is None:
        return {"empty": True, "reason": "STOCK_MAPPING_MISSING", "shop_id": shop_id, "platform_sku": platform_sku}
    return {"empty": False, "rule": dict(rule), "task": dict(task) if task else None}


@app.get("/internal/process-records/{external_order_id}")
def internal_process_records(external_order_id: str, shop_id: str, x_company_id: Annotated[str, Header()], x_service_token: Annotated[str | None, Header()] = None) -> dict[str, object]:
    require_token(x_service_token, "support-read")
    with get_connection() as connection:
        row = connection.execute(
            """SELECT r.event_id::text, r.received_at, r.payload->>'sku' AS platform_sku,
            (r.payload->>'quantity')::integer AS source_quantity, (r.payload->>'amount_minor')::integer AS source_amount_minor,
            t.task_id::text, t.status AS task_status, t.error_code, t.http_status, t.failure_request_id::text, t.attempts,
            m.merchant_order_id::text, m.merchant_sku, m.quantity, m.amount_minor,
            (SELECT COUNT(*) FROM merchant.orders c WHERE c.company_id = r.company_id AND c.shop_id = r.shop_id AND c.external_order_id = r.external_order_id) AS merchant_order_count
            FROM merchant.order_event_receipts r
            JOIN merchant.order_tasks t ON t.event_id = r.event_id
            LEFT JOIN merchant.orders m ON m.event_id = r.event_id
            WHERE r.company_id = %s AND r.shop_id = %s AND r.external_order_id = %s""",
            (x_company_id, shop_id, external_order_id),
        ).fetchone()
    if row is None:
        return {"empty": True, "shop_id": shop_id, "external_order_id": external_order_id, "receipt": None, "task": None, "merchant_order": None}
    result = dict(row)
    result["empty"] = False
    return result


@app.get("/internal/shops/{shop_id}/mappings/{platform_sku}")
def internal_sku_mapping(shop_id: str, platform_sku: str, x_company_id: Annotated[str, Header()], x_service_token: Annotated[str | None, Header()] = None) -> dict[str, object]:
    require_token(x_service_token, "support-read")
    with get_connection() as connection:
        row = connection.execute("SELECT company_id, shop_id, platform_sku, merchant_sku, active FROM merchant.sku_mappings WHERE company_id = %s AND shop_id = %s AND platform_sku = %s", (x_company_id, shop_id, platform_sku)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="SKU mapping not found")
    return dict(row)


@app.post("/repairs/orders")
@traceable(name="merchant_accept_order_repair", run_type="tool")
def receive_order_repair(request: OrderRepairRequest, x_service_token: str | None = Header(default=None)) -> dict[str, object]:
    require_token(x_service_token, "support-write")
    if request.approval_expires_at <= datetime.now(UTC):
        raise HTTPException(status_code=409, detail="Approval expired")
    canonical = json.dumps(request.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    request_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    with get_connection() as connection:
        existing = connection.execute("""SELECT r.request_hash, r.receipt_id::text, t.task_id::text, t.status
            FROM merchant.order_repair_receipts r JOIN merchant.order_recovery_tasks t ON t.receipt_id = r.receipt_id
            WHERE r.action_id = %s""", (request.action_id,)).fetchone()
        if existing:
            if existing["request_hash"] != request_hash:
                raise HTTPException(status_code=409, detail="Action identifier already exists with different content")
            return {"accepted": True, "duplicate": True, "receipt_id": existing["receipt_id"], "task_id": existing["task_id"], "task_status": existing["status"]}
        receipt_id = str(uuid4())
        task_id = str(uuid4())
        connection.execute(
            """INSERT INTO merchant.order_repair_receipts (receipt_id, action_id, request_id, company_id, shop_id, external_order_id, source_event_id, source_version, shop_version, source_snapshot, enable_order_sync, approval_id, approved_by, approval_expires_at, request_hash, trace_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s)""",
            (receipt_id, request.action_id, request.request_id, request.company_id, request.shop_id, request.external_order_id, request.source_event_id, request.source_version, request.shop_version, json.dumps(request.source_snapshot), request.enable_order_sync, request.approval_id, request.approved_by, request.approval_expires_at, request_hash, current_trace_id()),
        )
        connection.execute("INSERT INTO merchant.order_recovery_tasks (task_id, receipt_id) VALUES (%s, %s)", (task_id, receipt_id))
    return {"accepted": True, "duplicate": False, "receipt_id": receipt_id, "task_id": task_id, "task_status": "pending"}


@app.get("/repairs/orders/{action_id}")
def get_order_repair(action_id: str, x_company_id: Annotated[str, Header()], x_service_token: Annotated[str | None, Header()] = None) -> dict[str, object]:
    require_token(x_service_token, "support-read")
    with get_connection() as connection:
        row = connection.execute("""SELECT r.receipt_id::text, r.action_id::text, r.request_id::text, r.company_id, r.shop_id, r.external_order_id,
            t.task_id::text, t.status AS task_status, t.error_code, t.attempts, t.result
            FROM merchant.order_repair_receipts r JOIN merchant.order_recovery_tasks t ON t.receipt_id = r.receipt_id
            WHERE r.action_id = %s AND r.company_id = %s""", (action_id, x_company_id)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Repair receipt not found")
    return dict(row)


@app.post("/orders/{external_order_id}/dispatch")
def dispatch_order(external_order_id: str, shop_id: str, auth: Annotated[BusinessAuth, Depends(current_user)]) -> dict[str, object]:
    with get_connection() as connection:
        order = connection.execute("SELECT merchant_order_id::text FROM merchant.orders WHERE company_id = %s AND shop_id = %s AND external_order_id = %s", (auth.company_id, shop_id, external_order_id)).fetchone()
        if order is None:
            raise HTTPException(status_code=404, detail="Merchant order not found")
        existing = connection.execute("SELECT task_id::text, status FROM merchant.warehouse_dispatch_tasks WHERE merchant_order_id = %s", (order["merchant_order_id"],)).fetchone()
        if existing:
            return {"duplicate": True, **dict(existing)}
        task_id = str(uuid4())
        connection.execute("INSERT INTO merchant.warehouse_dispatch_tasks (task_id, merchant_order_id, company_id, shop_id, external_order_id) VALUES (%s, %s, %s, %s, %s)", (task_id, order["merchant_order_id"], auth.company_id, shop_id, external_order_id))
    return {"duplicate": False, "task_id": task_id, "status": "pending"}


@app.post("/lab/shops/{shop_id}/shipment-sync")
def set_shipment_sync(shop_id: str, update: ShopSyncUpdate, company_id: str = "company-a", x_lab_token: str | None = Header(default=None)) -> dict[str, object]:
    require_token(x_lab_token, "lab-control")
    with get_connection() as connection:
        row = connection.execute("UPDATE merchant.shops SET shipment_sync_enabled = %s, version = version + 1 WHERE company_id = %s AND shop_id = %s RETURNING company_id, shop_id, shipment_sync_enabled, version", (update.enabled, company_id, shop_id)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Shop not found")
    return dict(row)


@app.post("/events/shipments")
@traceable(name="merchant_receive_shipment_event", run_type="tool")
def receive_shipment_event(event: ShipmentEvent, x_service_token: str | None = Header(default=None)) -> dict[str, object]:
    require_token(x_service_token, "merchant-shipment")
    canonical = json.dumps(event.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    request_id = str(uuid5(NAMESPACE_URL, f"platform-shipment:{event.company_id}:{event.shipment_id}"))
    with get_connection() as connection:
        order = connection.execute("SELECT 1 FROM merchant.orders WHERE company_id = %s AND shop_id = %s AND external_order_id = %s", (event.company_id, event.shop_id, event.external_order_id)).fetchone()
        if order is None:
            raise HTTPException(status_code=409, detail="Merchant order not found")
        existing = connection.execute("SELECT payload_hash FROM merchant.shipment_event_receipts WHERE shipment_id = %s", (event.shipment_id,)).fetchone()
        if existing:
            if existing["payload_hash"] != content_hash:
                raise HTTPException(status_code=409, detail="Shipment identifier already exists with different content")
            task = connection.execute("SELECT task_id::text, status FROM merchant.shipment_tasks WHERE shipment_id = %s", (event.shipment_id,)).fetchone()
            return {"accepted": True, "duplicate": True, "task_id": task["task_id"], "task_status": task["status"]}
        task_id = str(uuid4())
        connection.execute("INSERT INTO merchant.shipment_event_receipts (shipment_id, company_id, shop_id, external_order_id, payload_hash, payload, trace_id) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)", (event.shipment_id, event.company_id, event.shop_id, event.external_order_id, content_hash, canonical, current_trace_id()))
        connection.execute("INSERT INTO merchant.shipment_tasks (task_id, shipment_id, company_id, shop_id, external_order_id, request_id) VALUES (%s, %s, %s, %s, %s, %s)", (task_id, event.shipment_id, event.company_id, event.shop_id, event.external_order_id, request_id))
    return {"accepted": True, "duplicate": False, "task_id": task_id, "task_status": "pending"}


@app.get("/internal/shipment-records/{external_order_id}")
def internal_shipment_records(external_order_id: str, shop_id: str, x_company_id: Annotated[str, Header()], x_service_token: Annotated[str | None, Header()] = None) -> dict[str, object]:
    require_token(x_service_token, "support-read")
    with get_connection() as connection:
        row = connection.execute("""SELECT r.shipment_id::text, r.received_at, r.payload->>'carrier' AS event_carrier, r.payload->>'tracking_number' AS event_tracking_number,
            t.task_id::text, t.status AS task_status, t.error_code, t.http_status, t.failure_request_id::text, t.attempts, t.request_id::text, t.platform_receipt,
            s.merchant_shipment_id::text, s.carrier, s.tracking_number, s.version, s.received_at AS merchant_received_at
            FROM merchant.shipment_event_receipts r JOIN merchant.shipment_tasks t ON t.shipment_id = r.shipment_id
            LEFT JOIN merchant.shipments s ON s.shipment_id = r.shipment_id
            WHERE r.company_id = %s AND r.shop_id = %s AND r.external_order_id = %s""", (x_company_id, shop_id, external_order_id)).fetchone()
    if row is None:
        return {"empty": True, "shop_id": shop_id, "external_order_id": external_order_id}
    result = dict(row)
    result["empty"] = False
    return result


@app.post("/repairs/shipments")
@traceable(name="merchant_receive_shipment_repair", run_type="tool")
def receive_shipment_repair(request: ShipmentRepairRequest, x_service_token: str | None = Header(default=None)) -> dict[str, object]:
    require_token(x_service_token, "support-write")
    canonical = json.dumps(request.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    request_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    with get_connection() as connection:
        existing = connection.execute("SELECT receipt_id::text, request_hash FROM merchant.shipment_repair_receipts WHERE action_id = %s OR request_id = %s", (request.action_id, request.request_id)).fetchone()
        if existing:
            if existing["request_hash"] != request_hash:
                raise HTTPException(status_code=409, detail="Shipment repair identifier already exists with different content")
            task = connection.execute("SELECT task_id::text, status FROM merchant.shipment_recovery_tasks WHERE receipt_id = %s", (existing["receipt_id"],)).fetchone()
            return {"accepted": True, "duplicate": True, "receipt_id": existing["receipt_id"], "task_id": task["task_id"], "task_status": task["status"]}
        shipment = connection.execute("SELECT shipment_id::text, version, carrier, tracking_number FROM merchant.shipments WHERE shipment_id = %s AND company_id = %s AND shop_id = %s AND external_order_id = %s", (request.shipment_id, request.company_id, request.shop_id, request.external_order_id)).fetchone()
        if shipment is None:
            raise HTTPException(status_code=409, detail="Merchant shipment fact not found")
        receipt_id = str(uuid4())
        task_id = str(uuid4())
        connection.execute("""INSERT INTO merchant.shipment_repair_receipts
            (receipt_id, action_id, request_id, company_id, shop_id, external_order_id, shipment_id, shipment_version, source_snapshot, enable_shipment_sync, approval_id, approved_by, approval_expires_at, request_hash, trace_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s)""", (receipt_id, request.action_id, request.request_id, request.company_id, request.shop_id, request.external_order_id, request.shipment_id, request.shipment_version, json.dumps(request.source_snapshot, default=str), request.enable_shipment_sync, request.approval_id, request.approved_by, request.approval_expires_at, request_hash, current_trace_id()))
        connection.execute("INSERT INTO merchant.shipment_recovery_tasks (task_id, receipt_id) VALUES (%s, %s)", (task_id, receipt_id))
    return {"accepted": True, "duplicate": False, "receipt_id": receipt_id, "task_id": task_id, "task_status": "pending"}


@app.get("/repairs/shipments/{action_id}")
def get_shipment_repair(action_id: str, x_company_id: Annotated[str, Header()], x_service_token: Annotated[str | None, Header()] = None) -> dict[str, object]:
    require_token(x_service_token, "support-read")
    with get_connection() as connection:
        row = connection.execute("""SELECT r.receipt_id::text, r.action_id::text, r.request_id::text, r.company_id, r.shop_id, r.external_order_id,
            t.task_id::text, t.status AS task_status, t.error_code, t.attempts, t.result
            FROM merchant.shipment_repair_receipts r JOIN merchant.shipment_recovery_tasks t ON t.receipt_id = r.receipt_id
            WHERE r.action_id = %s AND r.company_id = %s""", (action_id, x_company_id)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Shipment repair receipt not found")
    return dict(row)
