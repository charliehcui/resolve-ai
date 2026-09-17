import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException
from langsmith import traceable

from app.db import get_connection
from app.trace import current_trace_id
from services.common import BusinessAuth, OrderEvent, OrderRepairRequest, ShopSyncUpdate, current_user, payload_hash, require_token

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
        row = connection.execute("SELECT task_id::text, event_id::text, company_id, shop_id, external_order_id, status, error_code, attempts, updated_at FROM merchant.order_tasks WHERE event_id = %s AND company_id = %s", (event_id, auth.company_id)).fetchone()
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


@app.get("/internal/shops/{shop_id}/status")
def internal_shop_status(shop_id: str, x_company_id: Annotated[str, Header()], x_service_token: Annotated[str | None, Header()] = None) -> dict[str, object]:
    require_token(x_service_token, "support-read")
    with get_connection() as connection:
        row = connection.execute("SELECT company_id, shop_id, channel, sync_enabled, version FROM merchant.shops WHERE company_id = %s AND shop_id = %s", (x_company_id, shop_id)).fetchone()
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


@app.get("/internal/process-records/{external_order_id}")
def internal_process_records(external_order_id: str, shop_id: str, x_company_id: Annotated[str, Header()], x_service_token: Annotated[str | None, Header()] = None) -> dict[str, object]:
    require_token(x_service_token, "support-read")
    with get_connection() as connection:
        row = connection.execute(
            """SELECT r.event_id::text, r.received_at, r.payload->>'sku' AS platform_sku,
            (r.payload->>'quantity')::integer AS source_quantity, (r.payload->>'amount_minor')::integer AS source_amount_minor,
            t.task_id::text, t.status AS task_status, t.error_code, t.attempts,
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
