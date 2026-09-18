import hashlib
import json
import os
from typing import Annotated
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from langsmith import traceable

from backend.app.database import get_connection
from backend.app.trace import current_trace_id
from simulator.services.common import BusinessAuth, ShipmentCreate, ShipmentEvent, WarehouseOrderRequest, WarehouseStockUpdate, current_user, read_service_token, require_token

app = FastAPI(title="ResolveAI Warehouse Simulator")


def event_hash(event: ShipmentEvent) -> str:
    value = json.dumps(event.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/orders")
@traceable(name="warehouse_receive_order", run_type="tool")
def receive_order(request: WarehouseOrderRequest, x_service_token: str | None = Header(default=None)) -> dict[str, object]:
    require_token(x_service_token, "warehouse-ingest")
    with get_connection() as connection:
        existing = connection.execute("SELECT warehouse_order_id::text, merchant_order_id::text, merchant_sku, quantity, status FROM warehouse.orders WHERE company_id = %s AND shop_id = %s AND external_order_id = %s", (request.company_id, request.shop_id, request.external_order_id)).fetchone()
        if existing:
            if existing["merchant_order_id"] != request.merchant_order_id or existing["merchant_sku"] != request.merchant_sku or existing["quantity"] != request.quantity:
                raise HTTPException(status_code=409, detail="Warehouse order already exists with different content")
            return {"accepted": True, "duplicate": True, **dict(existing)}
        warehouse_order_id = str(uuid4())
        connection.execute("INSERT INTO warehouse.orders (warehouse_order_id, merchant_order_id, company_id, shop_id, external_order_id, merchant_sku, quantity) VALUES (%s, %s, %s, %s, %s, %s, %s)", (warehouse_order_id, request.merchant_order_id, request.company_id, request.shop_id, request.external_order_id, request.merchant_sku, request.quantity))
    return {"accepted": True, "duplicate": False, "warehouse_order_id": warehouse_order_id, "merchant_order_id": request.merchant_order_id, "status": "awaiting_shipment"}


def deliver_shipment(event: ShipmentEvent) -> tuple[str, int | None, dict[str, object] | None, str | None]:
    merchant_url = os.getenv("MERCHANT_SHIPMENT_URL", "http://127.0.0.1:8002/events/shipments")
    try:
        response = httpx.post(merchant_url, json=event.model_dump(mode="json"), headers={"X-Service-Token": read_service_token("merchant-shipment")}, timeout=5)
        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else None
        if response.is_success:
            return "delivered", response.status_code, body, None
        return "failed", response.status_code, body, f"HTTP_{response.status_code}"
    except httpx.RequestError as error:
        return "failed", None, None, type(error).__name__


@app.post("/shipments")
@traceable(name="warehouse_create_shipment", run_type="tool")
def create_shipment(request: ShipmentCreate, auth: Annotated[BusinessAuth, Depends(current_user)]) -> dict[str, object]:
    with get_connection() as connection:
        order = connection.execute("SELECT warehouse_order_id::text, status, version FROM warehouse.orders WHERE company_id = %s AND shop_id = %s AND external_order_id = %s FOR UPDATE", (auth.company_id, request.shop_id, request.external_order_id)).fetchone()
        if order is None:
            raise HTTPException(status_code=409, detail="Warehouse order has not been received")
        existing = connection.execute("SELECT shipment_id::text, carrier, tracking_number, version, shipped_at FROM warehouse.shipments WHERE warehouse_order_id = %s", (order["warehouse_order_id"],)).fetchone()
        if existing:
            if existing["carrier"] != request.carrier or existing["tracking_number"] != request.tracking_number:
                raise HTTPException(status_code=409, detail="Order was already shipped with different tracking data")
            shipment = dict(existing)
            duplicate = True
        else:
            shipment_id = str(uuid4())
            shipment = connection.execute("INSERT INTO warehouse.shipments (shipment_id, warehouse_order_id, company_id, shop_id, external_order_id, carrier, tracking_number) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING shipment_id::text, carrier, tracking_number, version, shipped_at", (shipment_id, order["warehouse_order_id"], auth.company_id, request.shop_id, request.external_order_id, request.carrier, request.tracking_number)).fetchone()
            connection.execute("UPDATE warehouse.orders SET status = 'shipped', version = version + 1, updated_at = NOW() WHERE warehouse_order_id = %s", (order["warehouse_order_id"],))
            duplicate = False
    event = ShipmentEvent(shipment_id=shipment["shipment_id"], company_id=auth.company_id, shop_id=request.shop_id, external_order_id=request.external_order_id, carrier=shipment["carrier"], tracking_number=shipment["tracking_number"], version=shipment["version"], shipped_at=shipment["shipped_at"])
    if duplicate:
        with get_connection() as connection:
            delivery = connection.execute("SELECT status, http_status, response, error_type FROM warehouse.shipment_deliveries WHERE shipment_id = %s ORDER BY created_at DESC LIMIT 1", (event.shipment_id,)).fetchone()
        return {"shipment_id": event.shipment_id, "duplicate": True, "warehouse_status": "shipped", "delivery": dict(delivery) if delivery else None}
    status, http_status, response, error_type = deliver_shipment(event)
    with get_connection() as connection:
        connection.execute("INSERT INTO warehouse.shipment_deliveries (delivery_id, shipment_id, status, http_status, response, error_type, trace_id) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)", (str(uuid4()), event.shipment_id, status, http_status, json.dumps(response) if response is not None else None, error_type, current_trace_id()))
    return {"shipment_id": event.shipment_id, "duplicate": False, "warehouse_status": "shipped", "delivery_status": status, "delivery_http_status": http_status, "merchant_result": response, "delivery_error": error_type}


def shipment_fact(company_id: str, shop_id: str, external_order_id: str) -> dict[str, object] | None:
    with get_connection() as connection:
        row = connection.execute("""SELECT o.warehouse_order_id::text, o.merchant_order_id::text, o.company_id, o.shop_id, o.external_order_id, o.merchant_sku, o.quantity,
            o.status AS warehouse_order_status, o.version AS warehouse_order_version, s.shipment_id::text, s.carrier, s.tracking_number, s.version AS shipment_version, s.shipped_at,
            (SELECT COUNT(*) FROM warehouse.shipments c WHERE c.warehouse_order_id = o.warehouse_order_id) AS shipment_count
            FROM warehouse.orders o LEFT JOIN warehouse.shipments s ON s.warehouse_order_id = o.warehouse_order_id
            WHERE o.company_id = %s AND o.shop_id = %s AND o.external_order_id = %s""", (company_id, shop_id, external_order_id)).fetchone()
    return dict(row) if row else None


@app.get("/orders/{external_order_id}")
def get_order(external_order_id: str, shop_id: str, auth: Annotated[BusinessAuth, Depends(current_user)]) -> dict[str, object]:
    row = shipment_fact(auth.company_id, shop_id, external_order_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Warehouse order not found")
    return row


@app.get("/internal/shipments/{external_order_id}")
def internal_shipment(external_order_id: str, shop_id: str, x_company_id: Annotated[str, Header()], x_service_token: Annotated[str | None, Header()] = None) -> dict[str, object]:
    require_token(x_service_token, "support-read")
    row = shipment_fact(x_company_id, shop_id, external_order_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Warehouse order not found")
    return row


@app.post("/lab/stocks/{warehouse_sku}")
def set_stock(warehouse_sku: str, update: WarehouseStockUpdate, company_id: str = "company-a", x_lab_token: str | None = Header(default=None)) -> dict[str, object]:
    require_token(x_lab_token, "lab-control")
    with get_connection() as connection:
        existing = connection.execute("SELECT physical_quantity, reserved_quantity FROM warehouse.stock_items WHERE company_id = %s AND warehouse_sku = %s", (company_id, warehouse_sku)).fetchone()
        if existing is None:
            row = connection.execute("INSERT INTO warehouse.stock_items (company_id, warehouse_sku, physical_quantity, reserved_quantity) VALUES (%s, %s, %s, %s) RETURNING company_id, warehouse_sku, physical_quantity, reserved_quantity, version, updated_at", (company_id, warehouse_sku, update.physical_quantity, update.reserved_quantity)).fetchone()
        elif existing["physical_quantity"] == update.physical_quantity and existing["reserved_quantity"] == update.reserved_quantity:
            row = connection.execute("SELECT company_id, warehouse_sku, physical_quantity, reserved_quantity, version, updated_at FROM warehouse.stock_items WHERE company_id = %s AND warehouse_sku = %s", (company_id, warehouse_sku)).fetchone()
        else:
            row = connection.execute("UPDATE warehouse.stock_items SET physical_quantity = %s, reserved_quantity = %s, version = version + 1, updated_at = NOW() WHERE company_id = %s AND warehouse_sku = %s RETURNING company_id, warehouse_sku, physical_quantity, reserved_quantity, version, updated_at", (update.physical_quantity, update.reserved_quantity, company_id, warehouse_sku)).fetchone()
    return dict(row)


@app.get("/internal/stocks/{warehouse_sku}")
def internal_stock(warehouse_sku: str, x_company_id: Annotated[str, Header()], x_service_token: Annotated[str | None, Header()] = None) -> dict[str, object]:
    require_token(x_service_token, "support-read")
    with get_connection() as connection:
        row = connection.execute("SELECT company_id, warehouse_sku, physical_quantity, reserved_quantity, version, updated_at FROM warehouse.stock_items WHERE company_id = %s AND warehouse_sku = %s", (x_company_id, warehouse_sku)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Warehouse stock not found")
    return dict(row)
