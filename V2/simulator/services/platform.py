import hashlib
import json
import os
import time
from typing import Annotated
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from langsmith import traceable

from backend.app.database import get_connection
from backend.app.trace import current_trace_id
from simulator.services.common import BusinessAuth, OrderCreate, OrderEvent, PlatformShipmentUpdate, PlatformStockUpdate, current_user, payload_hash, read_service_token, require_token

app = FastAPI(title="ResolveAI Platform Simulator")


@traceable(name="platform_deliver_order_event", run_type="tool")
def deliver_event(event: OrderEvent) -> tuple[str, int | None, dict[str, object] | None, str | None]:
    url = os.getenv("MERCHANT_INGEST_URL", "http://127.0.0.1:8002/events/orders")
    try:
        response = httpx.post(url, json=event.model_dump(), headers={"X-Service-Token": read_service_token("merchant-ingest")}, timeout=5)
        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else None
        if response.is_success:
            return "delivered", response.status_code, body, None
        return "failed", response.status_code, body, f"HTTP_{response.status_code}"
    except httpx.RequestError as error:
        return "failed", None, None, type(error).__name__


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/orders")
@traceable(name="platform_create_order", run_type="tool")
def create_order(order: OrderCreate, auth: Annotated[BusinessAuth, Depends(current_user)]) -> dict[str, object]:
    if order.payment_status not in {"paid", "unpaid", "cancelled"}:
        raise HTTPException(status_code=422, detail="Unsupported payment status")
    with get_connection() as connection:
        existing = connection.execute(
            "SELECT platform_order_id::text, event_id::text, sku, quantity, amount_minor, payment_status, payload_hash, version FROM platform.orders WHERE company_id = %s AND shop_id = %s AND external_order_id = %s",
            (auth.company_id, order.shop_id, order.external_order_id),
        ).fetchone()
        event_id = existing["event_id"] if existing else str(uuid4())
        event = OrderEvent(event_id=event_id, company_id=auth.company_id, **order.model_dump())
        content_hash = payload_hash(event)
        if existing and existing["payload_hash"] != content_hash:
            raise HTTPException(status_code=409, detail="Order identifier already exists with different content")
        platform_order_id = existing["platform_order_id"] if existing else str(uuid4())
        if existing is None:
            connection.execute(
                "INSERT INTO platform.orders (platform_order_id, event_id, company_id, shop_id, external_order_id, sku, quantity, amount_minor, payment_status, payload_hash) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (platform_order_id, event_id, auth.company_id, order.shop_id, order.external_order_id, order.sku, order.quantity, order.amount_minor, order.payment_status, content_hash),
            )
    status, http_status, merchant_result, error_type = deliver_event(event)
    trace_id = current_trace_id()
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO platform.event_deliveries (delivery_id, event_id, status, http_status, error_type, trace_id) VALUES (%s, %s, %s, %s, %s, %s)",
            (str(uuid4()), event_id, status, http_status, error_type, trace_id),
        )
    return {"platform_order_id": platform_order_id, "event_id": event_id, "duplicate_order": existing is not None, "delivery_status": status, "delivery_http_status": http_status, "merchant_result": merchant_result, "delivery_error": error_type, "trace_id": trace_id}


@app.get("/orders/{external_order_id}")
def get_order(external_order_id: str, shop_id: str, auth: Annotated[BusinessAuth, Depends(current_user)]) -> dict[str, object]:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT platform_order_id::text, event_id::text, company_id, shop_id, external_order_id, sku, quantity, amount_minor, payment_status, version, created_at FROM platform.orders WHERE company_id = %s AND shop_id = %s AND external_order_id = %s",
            (auth.company_id, shop_id, external_order_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Platform order not found")
    return dict(row)


@app.get("/internal/orders/{external_order_id}")
def internal_get_order(external_order_id: str, shop_id: str, x_company_id: Annotated[str, Header()], x_service_token: Annotated[str | None, Header()] = None) -> dict[str, object]:
    require_token(x_service_token, "support-read")
    with get_connection() as connection:
        row = connection.execute(
            "SELECT platform_order_id::text, event_id::text, company_id, shop_id, external_order_id, sku, quantity, amount_minor, payment_status, version, created_at FROM platform.orders WHERE company_id = %s AND shop_id = %s AND external_order_id = %s",
            (x_company_id, shop_id, external_order_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Platform order not found")
    return dict(row)


@app.post("/internal/shipments")
@traceable(name="platform_accept_shipment", run_type="tool")
def accept_shipment(update: PlatformShipmentUpdate, x_service_token: str | None = Header(default=None)) -> dict[str, object]:
    require_token(x_service_token, "platform-shipment")
    canonical = json.dumps(update.model_dump(mode="json", exclude={"request_id"}), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    with get_connection() as connection:
        source = connection.execute("SELECT payment_status FROM platform.orders WHERE company_id = %s AND shop_id = %s AND external_order_id = %s", (update.company_id, update.shop_id, update.external_order_id)).fetchone()
        if source is None:
            raise HTTPException(status_code=404, detail="Platform order not found")
        if source["payment_status"] == "cancelled":
            raise HTTPException(status_code=409, detail="Cancelled order cannot accept shipment")
        existing = connection.execute("SELECT request_id::text, shipment_id::text, carrier, tracking_number, payload_hash, status, version FROM platform.shipments WHERE company_id = %s AND shop_id = %s AND external_order_id = %s", (update.company_id, update.shop_id, update.external_order_id)).fetchone()
        if existing:
            if existing["payload_hash"] != content_hash or existing["request_id"] != update.request_id:
                raise HTTPException(status_code=409, detail="Platform shipment already exists with different content")
            return {"accepted": True, "duplicate": True, **dict(existing)}
        platform_shipment_id = str(uuid4())
        row = connection.execute("""INSERT INTO platform.shipments (platform_shipment_id, request_id, shipment_id, company_id, shop_id, external_order_id, carrier, tracking_number, payload_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING platform_shipment_id::text, request_id::text, shipment_id::text, carrier, tracking_number, status, version, updated_at""", (platform_shipment_id, update.request_id, update.shipment_id, update.company_id, update.shop_id, update.external_order_id, update.carrier, update.tracking_number, content_hash)).fetchone()
    with get_connection() as connection:
        delay = connection.execute("""UPDATE platform.response_delay_controls SET remaining = remaining - 1
            WHERE company_id = %s AND shop_id = %s AND operation = 'shipment' AND remaining > 0
            RETURNING delay_seconds""", (update.company_id, update.shop_id)).fetchone()
    if delay:
        time.sleep(float(delay["delay_seconds"]))
    return {"accepted": True, "duplicate": False, **dict(row)}


@app.post("/lab/shops/{shop_id}/shipment-response-lost")
def set_shipment_response_lost(shop_id: str, company_id: str = "company-a", delay_seconds: float = 6, x_lab_token: str | None = Header(default=None)) -> dict[str, object]:
    require_token(x_lab_token, "lab-control")
    with get_connection() as connection:
        connection.execute("""INSERT INTO platform.response_delay_controls (company_id, shop_id, operation, remaining, delay_seconds)
            VALUES (%s, %s, 'shipment', 1, %s)
            ON CONFLICT (company_id, shop_id, operation) DO UPDATE SET remaining = 1, delay_seconds = EXCLUDED.delay_seconds""", (company_id, shop_id, delay_seconds))
    return {"company_id": company_id, "shop_id": shop_id, "operation": "shipment", "remaining": 1, "delay_seconds": delay_seconds}


@app.get("/internal/shipments/{external_order_id}")
def internal_get_shipment(external_order_id: str, shop_id: str, x_company_id: Annotated[str, Header()], x_service_token: Annotated[str | None, Header()] = None) -> dict[str, object]:
    require_token(x_service_token, "support-read")
    with get_connection() as connection:
        row = connection.execute("SELECT platform_shipment_id::text, request_id::text, shipment_id::text, company_id, shop_id, external_order_id, carrier, tracking_number, status, version, updated_at FROM platform.shipments WHERE company_id = %s AND shop_id = %s AND external_order_id = %s", (x_company_id, shop_id, external_order_id)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Platform shipment not found")
    return dict(row)


@app.post("/internal/stocks")
@traceable(name="platform_publish_stock", run_type="tool")
def publish_stock(update: PlatformStockUpdate, x_service_token: str | None = Header(default=None)) -> dict[str, object]:
    require_token(x_service_token, "platform-stock")
    with get_connection() as connection:
        duplicate = connection.execute("SELECT platform_stock_id::text, request_id::text, quantity, source_version, updated_at FROM platform.stock_levels WHERE request_id = %s", (update.request_id,)).fetchone()
        if duplicate:
            return {"accepted": True, "duplicate": True, **dict(duplicate)}
        current = connection.execute("SELECT source_version FROM platform.stock_levels WHERE company_id = %s AND shop_id = %s AND platform_sku = %s FOR UPDATE", (update.company_id, update.shop_id, update.platform_sku)).fetchone()
        if current and current["source_version"] > update.source_version:
            raise HTTPException(status_code=409, detail="A newer stock version already exists")
        platform_stock_id = str(uuid4())
        row = connection.execute(
            """INSERT INTO platform.stock_levels (platform_stock_id, request_id, company_id, shop_id, platform_sku, quantity, source_version)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (company_id, shop_id, platform_sku) DO UPDATE SET request_id = EXCLUDED.request_id, quantity = EXCLUDED.quantity, source_version = EXCLUDED.source_version, updated_at = NOW()
            RETURNING platform_stock_id::text, request_id::text, company_id, shop_id, platform_sku, quantity, source_version, updated_at""",
            (platform_stock_id, update.request_id, update.company_id, update.shop_id, update.platform_sku, update.quantity, update.source_version),
        ).fetchone()
    return {"accepted": True, "duplicate": False, **dict(row)}


@app.get("/internal/stocks/{platform_sku}")
def internal_stock(platform_sku: str, shop_id: str, x_company_id: Annotated[str, Header()], x_service_token: Annotated[str | None, Header()] = None) -> dict[str, object]:
    require_token(x_service_token, "support-read")
    with get_connection() as connection:
        row = connection.execute("SELECT platform_stock_id::text, request_id::text, company_id, shop_id, platform_sku, quantity, source_version, updated_at FROM platform.stock_levels WHERE company_id = %s AND shop_id = %s AND platform_sku = %s", (x_company_id, shop_id, platform_sku)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Platform stock not found")
    return dict(row)
