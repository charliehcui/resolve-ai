import argparse
import json
import os
import time
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from langsmith import traceable

from backend.app.database import get_connection
from backend.app.trace import current_trace_id
from simulator.services.common import OrderEvent, payload_hash, read_service_token


def recover_interrupted_tasks() -> int:
    with get_connection() as connection:
        cursor = connection.execute("UPDATE merchant.order_tasks SET status = 'pending', error_code = NULL, updated_at = NOW() WHERE status = 'processing'")
        dispatch = connection.execute("UPDATE merchant.warehouse_dispatch_tasks SET status = 'pending', error_code = NULL, updated_at = NOW() WHERE status = 'processing'")
        shipment = connection.execute("UPDATE merchant.shipment_tasks SET status = 'pending', error_code = NULL, updated_at = NOW() WHERE status = 'processing'")
        recovery = connection.execute("UPDATE merchant.order_recovery_tasks SET status = 'pending', error_code = NULL, updated_at = NOW() WHERE status = 'processing'")
        shipment_recovery = connection.execute("UPDATE merchant.shipment_recovery_tasks SET status = 'pending', error_code = NULL, updated_at = NOW() WHERE status = 'processing'")
        stock = connection.execute("UPDATE merchant.stock_publish_tasks SET status = 'pending', error_code = NULL, updated_at = NOW() WHERE status = 'processing'")
    return cursor.rowcount + dispatch.rowcount + shipment.rowcount + recovery.rowcount + shipment_recovery.rowcount + stock.rowcount


def queue_dispatch(connection, event_id: str) -> None:
    order = connection.execute("SELECT merchant_order_id::text, company_id, shop_id, external_order_id FROM merchant.orders WHERE event_id = %s", (event_id,)).fetchone()
    if order:
        connection.execute("INSERT INTO merchant.warehouse_dispatch_tasks (task_id, merchant_order_id, company_id, shop_id, external_order_id) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (merchant_order_id) DO NOTHING", (str(uuid4()), order["merchant_order_id"], order["company_id"], order["shop_id"], order["external_order_id"]))


def finish_task(connection, task_id: str, status: str, error_code: str | None = None, http_status: int | None = None, failure_request_id: str | None = None) -> dict[str, object]:
    connection.execute(
        "UPDATE merchant.order_tasks SET status = %s, error_code = %s, http_status = %s, failure_request_id = %s, worker_trace_id = %s, updated_at = NOW() WHERE task_id = %s",
        (status, error_code, http_status, failure_request_id, current_trace_id(), task_id),
    )
    result = {"task_id": task_id, "status": status, "error_code": error_code}
    if http_status is not None or failure_request_id is not None:
        result.update({"http_status": http_status, "request_id": failure_request_id})
    return result


def channel_failure(connection_status: str) -> tuple[str, int | None, str] | None:
    failures = {
        "auth_expired": ("blocked", 401, "CHANNEL_AUTH_EXPIRED"),
        "forbidden": ("blocked", 403, "CHANNEL_FORBIDDEN"),
        "rate_limited": ("failed", 429, "CHANNEL_RATE_LIMITED"),
        "internal_error": ("failed", 500, "CHANNEL_INTERNAL_ERROR"),
        "unavailable": ("failed", 503, "CHANNEL_UNAVAILABLE"),
        "timeout": ("failed", None, "CHANNEL_TIMEOUT"),
    }
    if connection_status == "authorized":
        return None
    return failures.get(connection_status, ("blocked", 401, "SHOP_NOT_AUTHORIZED"))


def record_channel_failure(connection, task: dict[str, object], channel: str, operation: str, http_status: int | None, error_code: str, request_id: str) -> None:
    connection.execute("INSERT INTO merchant.channel_failures (failure_id, company_id, shop_id, channel, operation, http_status, error_code, request_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", (str(uuid4()), task["company_id"], task["shop_id"], channel, operation, http_status, error_code, request_id))


@traceable(name="merchant_process_order_task", run_type="tool")
def process_next_task() -> dict[str, object] | None:
    with get_connection() as connection:
        task = connection.execute(
            """SELECT t.task_id::text, t.event_id::text, t.company_id, t.shop_id, t.external_order_id, r.payload
            FROM merchant.order_tasks t JOIN merchant.order_event_receipts r ON r.event_id = t.event_id
            WHERE t.status = 'pending' ORDER BY t.created_at FOR UPDATE OF t SKIP LOCKED LIMIT 1"""
        ).fetchone()
        if task is None:
            return None
        connection.execute("UPDATE merchant.order_tasks SET status = 'processing', attempts = attempts + 1, updated_at = NOW() WHERE task_id = %s", (task["task_id"],))
        shop = connection.execute("SELECT sync_enabled, channel, connection_status FROM merchant.shops WHERE company_id = %s AND shop_id = %s", (task["company_id"], task["shop_id"])).fetchone()
        if shop is None:
            return finish_task(connection, task["task_id"], "failed", "UNKNOWN_SHOP")
        if not shop["sync_enabled"]:
            return finish_task(connection, task["task_id"], "blocked", "ORDER_SYNC_DISABLED")
        failure = channel_failure(shop["connection_status"])
        if failure:
            status, http_status, error_code = failure
            request_id = str(uuid4())
            record_channel_failure(connection, task, shop["channel"], "order_import", http_status, error_code, request_id)
            return finish_task(connection, task["task_id"], status, error_code, http_status, request_id)
        payload = task["payload"]
        if payload["payment_status"] != "paid":
            return finish_task(connection, task["task_id"], "blocked", "ORDER_NOT_PAID")
        mapping = connection.execute("SELECT merchant_sku FROM merchant.sku_mappings WHERE company_id = %s AND shop_id = %s AND platform_sku = %s AND active", (task["company_id"], task["shop_id"], payload["sku"])).fetchone()
        if mapping is None:
            return finish_task(connection, task["task_id"], "blocked", "SKU_MAPPING_MISSING")
        connection.execute(
            """INSERT INTO merchant.orders (merchant_order_id, event_id, company_id, shop_id, external_order_id, merchant_sku, quantity, amount_minor)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (event_id) DO NOTHING""",
            (str(uuid4()), task["event_id"], task["company_id"], task["shop_id"], task["external_order_id"], mapping["merchant_sku"], payload["quantity"], payload["amount_minor"]),
        )
        queue_dispatch(connection, task["event_id"])
        return finish_task(connection, task["task_id"], "completed")


def read_platform_order(company_id: str, shop_id: str, external_order_id: str) -> dict[str, object] | None:
    base_url = os.getenv("PLATFORM_URL", "http://127.0.0.1:8001")
    response = httpx.get(
        f"{base_url}/internal/orders/{external_order_id}",
        params={"shop_id": shop_id},
        headers={"X-Service-Token": read_service_token("support-read"), "X-Company-ID": company_id},
        timeout=5,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def finish_recovery_task(connection, task_id: str, status: str, error_code: str | None = None, result: dict[str, object] | None = None) -> dict[str, object]:
    connection.execute(
        "UPDATE merchant.order_recovery_tasks SET status = %s, error_code = %s, result = %s::jsonb, worker_trace_id = %s, updated_at = NOW() WHERE task_id = %s",
        (status, error_code, json.dumps(result) if result is not None else None, current_trace_id(), task_id),
    )
    return {"task_id": task_id, "status": status, "error_code": error_code, "result": result}


@traceable(name="merchant_process_order_recovery", run_type="tool")
def process_next_recovery_task() -> dict[str, object] | None:
    with get_connection() as connection:
        task = connection.execute(
            """SELECT t.task_id::text, r.receipt_id::text, r.company_id, r.shop_id, r.external_order_id,
            r.source_event_id::text, r.source_version, r.shop_version, r.source_snapshot, r.enable_order_sync, r.approval_expires_at
            FROM merchant.order_recovery_tasks t JOIN merchant.order_repair_receipts r ON r.receipt_id = t.receipt_id
            WHERE t.status = 'pending' ORDER BY t.created_at FOR UPDATE OF t SKIP LOCKED LIMIT 1"""
        ).fetchone()
        if task is None:
            return None
        connection.execute("UPDATE merchant.order_recovery_tasks SET status = 'processing', attempts = attempts + 1, updated_at = NOW() WHERE task_id = %s", (task["task_id"],))
        if task["approval_expires_at"] <= datetime.now(UTC):
            return finish_recovery_task(connection, task["task_id"], "blocked", "APPROVAL_EXPIRED")
        try:
            platform_order = read_platform_order(task["company_id"], task["shop_id"], task["external_order_id"])
        except httpx.HTTPError as error:
            return finish_recovery_task(connection, task["task_id"], "failed", type(error).__name__)
        snapshot = task["source_snapshot"]
        if platform_order is None:
            return finish_recovery_task(connection, task["task_id"], "blocked", "SOURCE_ORDER_NOT_FOUND")
        compared_fields = ("event_id", "version", "sku", "quantity", "amount_minor", "payment_status")
        if any(platform_order.get(field) != snapshot.get(field) for field in compared_fields):
            return finish_recovery_task(connection, task["task_id"], "blocked", "SOURCE_VERSION_CHANGED")
        if platform_order["payment_status"] != "paid":
            return finish_recovery_task(connection, task["task_id"], "blocked", "ORDER_NOT_PAID")
        shop = connection.execute("SELECT sync_enabled, channel, connection_status, version FROM merchant.shops WHERE company_id = %s AND shop_id = %s FOR UPDATE", (task["company_id"], task["shop_id"])).fetchone()
        if shop is None:
            return finish_recovery_task(connection, task["task_id"], "blocked", "UNKNOWN_SHOP")
        if shop["version"] != task["shop_version"]:
            return finish_recovery_task(connection, task["task_id"], "blocked", "SHOP_VERSION_CHANGED")
        if not shop["sync_enabled"] and not task["enable_order_sync"]:
            return finish_recovery_task(connection, task["task_id"], "blocked", "ORDER_SYNC_DISABLED")
        failure = channel_failure(shop["connection_status"])
        if failure:
            status, http_status, error_code = failure
            failure_request_id = str(uuid4())
            record_channel_failure(connection, task, shop["channel"], "order_recovery", http_status, error_code, failure_request_id)
            return finish_recovery_task(connection, task["task_id"], status, error_code, {"http_status": http_status, "request_id": failure_request_id})
        mapping = connection.execute("SELECT merchant_sku FROM merchant.sku_mappings WHERE company_id = %s AND shop_id = %s AND platform_sku = %s AND active", (task["company_id"], task["shop_id"], platform_order["sku"])).fetchone()
        if mapping is None:
            return finish_recovery_task(connection, task["task_id"], "blocked", "SKU_MAPPING_MISSING")
        if not shop["sync_enabled"] and task["enable_order_sync"]:
            connection.execute("UPDATE merchant.shops SET sync_enabled = TRUE, version = version + 1 WHERE company_id = %s AND shop_id = %s", (task["company_id"], task["shop_id"]))
        event = OrderEvent(event_id=platform_order["event_id"], company_id=task["company_id"], shop_id=task["shop_id"], external_order_id=task["external_order_id"], sku=platform_order["sku"], quantity=platform_order["quantity"], amount_minor=platform_order["amount_minor"], payment_status=platform_order["payment_status"])
        receipt = connection.execute("SELECT payload_hash FROM merchant.order_event_receipts WHERE event_id = %s", (event.event_id,)).fetchone()
        content_hash = payload_hash(event)
        if receipt and receipt["payload_hash"] != content_hash:
            return finish_recovery_task(connection, task["task_id"], "blocked", "EVENT_CONTENT_CONFLICT")
        if receipt is None:
            connection.execute("INSERT INTO merchant.order_event_receipts (event_id, company_id, shop_id, external_order_id, payload_hash, payload, trace_id) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)", (event.event_id, event.company_id, event.shop_id, event.external_order_id, content_hash, json.dumps(event.model_dump()), current_trace_id()))
        regular_task = connection.execute("SELECT task_id::text FROM merchant.order_tasks WHERE event_id = %s", (event.event_id,)).fetchone()
        if regular_task is None:
            connection.execute("INSERT INTO merchant.order_tasks (task_id, event_id, company_id, shop_id, external_order_id, status, attempts, worker_trace_id) VALUES (%s, %s, %s, %s, %s, 'completed', 1, %s)", (str(uuid4()), event.event_id, event.company_id, event.shop_id, event.external_order_id, current_trace_id()))
        else:
            connection.execute("UPDATE merchant.order_tasks SET status = 'completed', error_code = NULL, worker_trace_id = %s, updated_at = NOW() WHERE event_id = %s", (current_trace_id(), event.event_id))
        connection.execute("""INSERT INTO merchant.orders (merchant_order_id, event_id, company_id, shop_id, external_order_id, merchant_sku, quantity, amount_minor)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (event_id) DO NOTHING""", (str(uuid4()), event.event_id, event.company_id, event.shop_id, event.external_order_id, mapping["merchant_sku"], event.quantity, event.amount_minor))
        queue_dispatch(connection, event.event_id)
        order_count = connection.execute("SELECT COUNT(*) AS count FROM merchant.orders WHERE company_id = %s AND shop_id = %s AND external_order_id = %s", (event.company_id, event.shop_id, event.external_order_id)).fetchone()["count"]
        if order_count != 1:
            return finish_recovery_task(connection, task["task_id"], "failed", "ORDER_UNIQUENESS_FAILED")
        return finish_recovery_task(connection, task["task_id"], "completed", result={"event_id": event.event_id, "merchant_order_count": order_count})


def finish_dispatch_task(connection, task_id: str, status: str, error_code: str | None = None, receipt: dict[str, object] | None = None) -> dict[str, object]:
    connection.execute("UPDATE merchant.warehouse_dispatch_tasks SET status = %s, error_code = %s, receipt = %s::jsonb, updated_at = NOW() WHERE task_id = %s", (status, error_code, json.dumps(receipt) if receipt is not None else None, task_id))
    return {"task_id": task_id, "status": status, "error_code": error_code, "receipt": receipt}


@traceable(name="merchant_dispatch_order_to_warehouse", run_type="tool")
def process_next_dispatch_task() -> dict[str, object] | None:
    with get_connection() as connection:
        task = connection.execute("""SELECT t.task_id::text, t.merchant_order_id::text, t.company_id, t.shop_id, t.external_order_id, o.merchant_sku, o.quantity
            FROM merchant.warehouse_dispatch_tasks t JOIN merchant.orders o ON o.merchant_order_id = t.merchant_order_id
            WHERE t.status = 'pending' ORDER BY t.created_at FOR UPDATE OF t SKIP LOCKED LIMIT 1""").fetchone()
        if task is None:
            return None
        connection.execute("UPDATE merchant.warehouse_dispatch_tasks SET status = 'processing', attempts = attempts + 1, updated_at = NOW() WHERE task_id = %s", (task["task_id"],))
        warehouse_url = os.getenv("WAREHOUSE_URL", "http://127.0.0.1:8003")
        payload = {"merchant_order_id": task["merchant_order_id"], "company_id": task["company_id"], "shop_id": task["shop_id"], "external_order_id": task["external_order_id"], "merchant_sku": task["merchant_sku"], "quantity": task["quantity"]}
        try:
            response = httpx.post(f"{warehouse_url}/orders", json=payload, headers={"X-Service-Token": read_service_token("warehouse-ingest")}, timeout=5)
            body = response.json() if response.headers.get("content-type", "").startswith("application/json") else None
            if response.is_success:
                return finish_dispatch_task(connection, task["task_id"], "completed", receipt=body)
            return finish_dispatch_task(connection, task["task_id"], "failed", f"HTTP_{response.status_code}", body)
        except httpx.RequestError as error:
            return finish_dispatch_task(connection, task["task_id"], "failed", type(error).__name__)


def finish_shipment_task(connection, task_id: str, status: str, error_code: str | None = None, receipt: dict[str, object] | None = None, http_status: int | None = None, failure_request_id: str | None = None) -> dict[str, object]:
    connection.execute("UPDATE merchant.shipment_tasks SET status = %s, error_code = %s, platform_receipt = %s::jsonb, http_status = %s, failure_request_id = %s, worker_trace_id = %s, updated_at = NOW() WHERE task_id = %s", (status, error_code, json.dumps(receipt) if receipt is not None else None, http_status, failure_request_id, current_trace_id(), task_id))
    return {"task_id": task_id, "status": status, "error_code": error_code, "platform_receipt": receipt, "http_status": http_status, "request_id": failure_request_id}


@traceable(name="merchant_process_shipment_task", run_type="tool")
def process_next_shipment_task() -> dict[str, object] | None:
    with get_connection() as connection:
        task = connection.execute("""SELECT t.task_id::text, t.shipment_id::text, t.company_id, t.shop_id, t.external_order_id, t.request_id::text, r.payload
            FROM merchant.shipment_tasks t JOIN merchant.shipment_event_receipts r ON r.shipment_id = t.shipment_id
            WHERE t.status = 'pending' ORDER BY t.created_at FOR UPDATE OF t SKIP LOCKED LIMIT 1""").fetchone()
        if task is None:
            return None
        connection.execute("UPDATE merchant.shipment_tasks SET status = 'processing', attempts = attempts + 1, updated_at = NOW() WHERE task_id = %s", (task["task_id"],))
        payload = task["payload"]
        existing = connection.execute("SELECT carrier, tracking_number FROM merchant.shipments WHERE shipment_id = %s", (task["shipment_id"],)).fetchone()
        if existing and (existing["carrier"] != payload["carrier"] or existing["tracking_number"] != payload["tracking_number"]):
            return finish_shipment_task(connection, task["task_id"], "failed", "SHIPMENT_CONTENT_CONFLICT")
        connection.execute("""INSERT INTO merchant.shipments (merchant_shipment_id, shipment_id, company_id, shop_id, external_order_id, carrier, tracking_number, version)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (shipment_id) DO NOTHING""", (str(uuid4()), task["shipment_id"], task["company_id"], task["shop_id"], task["external_order_id"], payload["carrier"], payload["tracking_number"], payload["version"]))
        shop = connection.execute("SELECT shipment_sync_enabled, channel, connection_status FROM merchant.shops WHERE company_id = %s AND shop_id = %s", (task["company_id"], task["shop_id"])).fetchone()
        if shop is None:
            return finish_shipment_task(connection, task["task_id"], "failed", "UNKNOWN_SHOP")
        if not shop["shipment_sync_enabled"]:
            return finish_shipment_task(connection, task["task_id"], "blocked", "SHIPMENT_SYNC_DISABLED")
        failure = channel_failure(shop["connection_status"])
        if failure:
            status, http_status, error_code = failure
            failure_request_id = str(uuid4())
            record_channel_failure(connection, task, shop["channel"], "shipment_publish", http_status, error_code, failure_request_id)
            return finish_shipment_task(connection, task["task_id"], status, error_code, http_status=http_status, failure_request_id=failure_request_id)
        platform_url = os.getenv("PLATFORM_URL", "http://127.0.0.1:8001")
        update = {**payload, "request_id": task["request_id"]}
        try:
            response = httpx.post(f"{platform_url}/internal/shipments", json=update, headers={"X-Service-Token": read_service_token("platform-shipment")}, timeout=5)
            body = response.json() if response.headers.get("content-type", "").startswith("application/json") else None
            if response.is_success:
                return finish_shipment_task(connection, task["task_id"], "completed", receipt=body)
            return finish_shipment_task(connection, task["task_id"], "failed", f"HTTP_{response.status_code}", body, response.status_code, str(uuid4()))
        except httpx.TimeoutException:
            return finish_shipment_task(connection, task["task_id"], "unknown", "PLATFORM_RESPONSE_LOST")
        except httpx.RequestError as error:
            return finish_shipment_task(connection, task["task_id"], "failed", type(error).__name__)


def finish_shipment_recovery_task(connection, task_id: str, status: str, error_code: str | None = None, result: dict[str, object] | None = None) -> dict[str, object]:
    connection.execute("UPDATE merchant.shipment_recovery_tasks SET status = %s, error_code = %s, result = %s::jsonb, worker_trace_id = %s, updated_at = NOW() WHERE task_id = %s", (status, error_code, json.dumps(result, default=str) if result is not None else None, current_trace_id(), task_id))
    return {"task_id": task_id, "status": status, "error_code": error_code, "result": result}


@traceable(name="merchant_process_shipment_recovery", run_type="tool")
def process_next_shipment_recovery_task() -> dict[str, object] | None:
    with get_connection() as connection:
        task = connection.execute("""SELECT t.task_id::text, r.request_id::text, r.company_id, r.shop_id, r.external_order_id,
            r.shipment_id::text, r.shipment_version, r.source_snapshot, r.enable_shipment_sync, r.approval_expires_at
            FROM merchant.shipment_recovery_tasks t JOIN merchant.shipment_repair_receipts r ON r.receipt_id = t.receipt_id
            WHERE t.status = 'pending' ORDER BY t.created_at FOR UPDATE OF t SKIP LOCKED LIMIT 1""").fetchone()
        if task is None:
            return None
        connection.execute("UPDATE merchant.shipment_recovery_tasks SET status = 'processing', attempts = attempts + 1, updated_at = NOW() WHERE task_id = %s", (task["task_id"],))
        if task["approval_expires_at"] <= datetime.now(UTC):
            return finish_shipment_recovery_task(connection, task["task_id"], "blocked", "APPROVAL_EXPIRED")
        shipment = connection.execute("SELECT shipment_id::text, carrier, tracking_number, version, received_at FROM merchant.shipments WHERE shipment_id = %s AND company_id = %s AND shop_id = %s AND external_order_id = %s", (task["shipment_id"], task["company_id"], task["shop_id"], task["external_order_id"])).fetchone()
        snapshot = task["source_snapshot"]
        if shipment is None:
            return finish_shipment_recovery_task(connection, task["task_id"], "blocked", "SHIPMENT_NOT_FOUND")
        if shipment["version"] != task["shipment_version"] or shipment["carrier"] != snapshot.get("carrier") or shipment["tracking_number"] != snapshot.get("tracking_number"):
            return finish_shipment_recovery_task(connection, task["task_id"], "blocked", "SHIPMENT_VERSION_CHANGED")
        shop = connection.execute("SELECT shipment_sync_enabled, channel, connection_status FROM merchant.shops WHERE company_id = %s AND shop_id = %s FOR UPDATE", (task["company_id"], task["shop_id"])).fetchone()
        if shop is None:
            return finish_shipment_recovery_task(connection, task["task_id"], "blocked", "UNKNOWN_SHOP")
        if not shop["shipment_sync_enabled"] and not task["enable_shipment_sync"]:
            return finish_shipment_recovery_task(connection, task["task_id"], "blocked", "SHIPMENT_SYNC_DISABLED")
        failure = channel_failure(shop["connection_status"])
        if failure:
            status, http_status, error_code = failure
            failure_request_id = str(uuid4())
            record_channel_failure(connection, task, shop["channel"], "shipment_recovery", http_status, error_code, failure_request_id)
            return finish_shipment_recovery_task(connection, task["task_id"], status, error_code, {"http_status": http_status, "request_id": failure_request_id})
        if not shop["shipment_sync_enabled"]:
            connection.execute("UPDATE merchant.shops SET shipment_sync_enabled = TRUE, version = version + 1 WHERE company_id = %s AND shop_id = %s", (task["company_id"], task["shop_id"]))
        update = {
            "request_id": task["request_id"],
            "shipment_id": task["shipment_id"],
            "company_id": task["company_id"],
            "shop_id": task["shop_id"],
            "external_order_id": task["external_order_id"],
            "carrier": shipment["carrier"],
            "tracking_number": shipment["tracking_number"],
            "version": shipment["version"],
            "shipped_at": snapshot["shipped_at"],
        }
        platform_url = os.getenv("PLATFORM_URL", "http://127.0.0.1:8001")
        try:
            response = httpx.post(f"{platform_url}/internal/shipments", json=update, headers={"X-Service-Token": read_service_token("platform-shipment")}, timeout=5)
            body = response.json() if response.headers.get("content-type", "").startswith("application/json") else None
            if response.is_success:
                connection.execute("UPDATE merchant.shipment_tasks SET status = 'completed', error_code = NULL, platform_receipt = %s::jsonb, worker_trace_id = %s, updated_at = NOW() WHERE shipment_id = %s", (json.dumps(body, default=str), current_trace_id(), task["shipment_id"]))
                return finish_shipment_recovery_task(connection, task["task_id"], "completed", result=body)
            return finish_shipment_recovery_task(connection, task["task_id"], "failed", f"HTTP_{response.status_code}", body)
        except httpx.TimeoutException:
            return finish_shipment_recovery_task(connection, task["task_id"], "unknown", "PLATFORM_RESPONSE_LOST")
        except httpx.RequestError as error:
            return finish_shipment_recovery_task(connection, task["task_id"], "failed", type(error).__name__)


def read_platform_shipment(company_id: str, shop_id: str, external_order_id: str) -> tuple[int, dict[str, object] | None]:
    platform_url = os.getenv("PLATFORM_URL", "http://127.0.0.1:8001")
    response = httpx.get(f"{platform_url}/internal/shipments/{external_order_id}", params={"shop_id": shop_id}, headers={"X-Service-Token": read_service_token("support-read"), "X-Company-ID": company_id}, timeout=5)
    body = response.json() if response.headers.get("content-type", "").startswith("application/json") else None
    return response.status_code, body


@traceable(name="merchant_reconcile_unknown_shipment", run_type="tool")
def reconcile_next_unknown_shipment() -> dict[str, object] | None:
    with get_connection() as connection:
        recovery = connection.execute("""SELECT t.task_id::text, r.company_id, r.shop_id, r.external_order_id, r.shipment_id::text, r.source_snapshot
            FROM merchant.shipment_recovery_tasks t JOIN merchant.shipment_repair_receipts r ON r.receipt_id = t.receipt_id
            WHERE t.status = 'unknown' ORDER BY t.updated_at FOR UPDATE OF t SKIP LOCKED LIMIT 1""").fetchone()
        regular = None if recovery else connection.execute("""SELECT t.task_id::text, t.company_id, t.shop_id, t.external_order_id, t.shipment_id::text, r.payload AS source_snapshot
            FROM merchant.shipment_tasks t JOIN merchant.shipment_event_receipts r ON r.shipment_id = t.shipment_id
            WHERE t.status = 'unknown' ORDER BY t.updated_at FOR UPDATE OF t SKIP LOCKED LIMIT 1""").fetchone()
        task = recovery or regular
        if task is None:
            return None
        try:
            status_code, platform = read_platform_shipment(task["company_id"], task["shop_id"], task["external_order_id"])
        except httpx.RequestError as error:
            return {"task_id": task["task_id"], "status": "unknown", "error_code": type(error).__name__}
        expected = task["source_snapshot"]
        matches = status_code == 200 and platform is not None and all(platform.get(field) == expected.get(field) for field in ("shipment_id", "carrier", "tracking_number"))
        if matches:
            receipt_json = json.dumps(platform, default=str)
            connection.execute("UPDATE merchant.shipment_tasks SET status = 'completed', error_code = NULL, platform_receipt = %s::jsonb, updated_at = NOW() WHERE shipment_id = %s", (receipt_json, task["shipment_id"]))
            if recovery:
                return finish_shipment_recovery_task(connection, task["task_id"], "completed", result={"reconciled": True, "platform": platform})
            return finish_shipment_task(connection, task["task_id"], "completed", receipt={"reconciled": True, "platform": platform})
        if status_code == 404:
            table = "merchant.shipment_recovery_tasks" if recovery else "merchant.shipment_tasks"
            connection.execute(f"UPDATE {table} SET status = 'pending', error_code = NULL, updated_at = NOW() WHERE task_id = %s", (task["task_id"],))
            return {"task_id": task["task_id"], "status": "pending", "reconciled": False}
        return {"task_id": task["task_id"], "status": "unknown", "error_code": f"HTTP_{status_code}"}


def finish_stock_task(connection, task_id: str, status: str, error_code: str | None = None, http_status: int | None = None, failure_request_id: str | None = None, receipt: dict[str, object] | None = None) -> dict[str, object]:
    connection.execute("UPDATE merchant.stock_publish_tasks SET status = %s, error_code = %s, http_status = %s, failure_request_id = %s, platform_receipt = %s::jsonb, updated_at = NOW() WHERE task_id = %s", (status, error_code, http_status, failure_request_id, json.dumps(receipt, default=str) if receipt else None, task_id))
    return {"task_id": task_id, "status": status, "error_code": error_code, "http_status": http_status, "request_id": failure_request_id, "receipt": receipt}


@traceable(name="merchant_publish_stock_task", run_type="tool")
def process_next_stock_task() -> dict[str, object] | None:
    with get_connection() as connection:
        task = connection.execute("""SELECT task_id::text, request_id::text, company_id, shop_id, platform_sku, warehouse_version, expected_quantity
            FROM merchant.stock_publish_tasks WHERE status = 'pending' ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1""").fetchone()
        if task is None:
            return None
        connection.execute("UPDATE merchant.stock_publish_tasks SET status = 'processing', attempts = attempts + 1, updated_at = NOW() WHERE task_id = %s", (task["task_id"],))
        shop = connection.execute("SELECT channel, connection_status FROM merchant.shops WHERE company_id = %s AND shop_id = %s", (task["company_id"], task["shop_id"])).fetchone()
        if shop is None:
            return finish_stock_task(connection, task["task_id"], "failed", "UNKNOWN_SHOP")
        failure = channel_failure(shop["connection_status"])
        if failure:
            status, http_status, error_code = failure
            failure_request_id = str(uuid4())
            record_channel_failure(connection, task, shop["channel"], "stock_publish", http_status, error_code, failure_request_id)
            return finish_stock_task(connection, task["task_id"], status, error_code, http_status, failure_request_id)
        payload = {"request_id": task["request_id"], "company_id": task["company_id"], "shop_id": task["shop_id"], "platform_sku": task["platform_sku"], "quantity": task["expected_quantity"], "source_version": task["warehouse_version"]}
        platform_url = os.getenv("PLATFORM_URL", "http://127.0.0.1:8001")
        try:
            response = httpx.post(f"{platform_url}/internal/stocks", json=payload, headers={"X-Service-Token": read_service_token("platform-stock")}, timeout=5)
            body = response.json() if response.headers.get("content-type", "").startswith("application/json") else None
            if response.is_success:
                return finish_stock_task(connection, task["task_id"], "completed", receipt=body)
            return finish_stock_task(connection, task["task_id"], "failed", f"HTTP_{response.status_code}", response.status_code, str(uuid4()), body)
        except httpx.TimeoutException:
            return finish_stock_task(connection, task["task_id"], "failed", "CHANNEL_TIMEOUT", failure_request_id=str(uuid4()))
        except httpx.RequestError as error:
            return finish_stock_task(connection, task["task_id"], "failed", type(error).__name__, failure_request_id=str(uuid4()))


def run_forever(poll_seconds: float = 0.5) -> None:
    recover_interrupted_tasks()
    while True:
        normal_result = process_next_task()
        recovery_result = process_next_recovery_task()
        dispatch_result = process_next_dispatch_task()
        shipment_result = process_next_shipment_task()
        shipment_recovery_result = process_next_shipment_recovery_task()
        reconciliation_result = reconcile_next_unknown_shipment()
        stock_result = process_next_stock_task()
        if normal_result is None and recovery_result is None and dispatch_result is None and shipment_result is None and shipment_recovery_result is None and reconciliation_result is None and stock_result is None:
            time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.once:
        print(process_next_task())
    else:
        run_forever()


if __name__ == "__main__":
    main()
