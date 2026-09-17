import os
from typing import Annotated
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from langsmith import traceable

from app.db import get_connection
from app.trace import current_trace_id
from services.common import BusinessAuth, OrderCreate, OrderEvent, current_user, payload_hash, read_service_token, require_token

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
