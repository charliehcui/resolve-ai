import argparse
import json
import os
import time
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from langsmith import traceable

from app.db import get_connection
from app.trace import current_trace_id
from services.common import OrderEvent, payload_hash, read_service_token


def recover_interrupted_tasks() -> int:
    with get_connection() as connection:
        cursor = connection.execute("UPDATE merchant.order_tasks SET status = 'pending', error_code = NULL, updated_at = NOW() WHERE status = 'processing'")
    return cursor.rowcount


def finish_task(connection, task_id: str, status: str, error_code: str | None = None) -> dict[str, object]:
    connection.execute(
        "UPDATE merchant.order_tasks SET status = %s, error_code = %s, worker_trace_id = %s, updated_at = NOW() WHERE task_id = %s",
        (status, error_code, current_trace_id(), task_id),
    )
    return {"task_id": task_id, "status": status, "error_code": error_code}


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
        if shop["channel"] == "B" and shop["connection_status"] != "authorized":
            return finish_task(connection, task["task_id"], "blocked", "SHOP_NOT_AUTHORIZED")
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
        if shop["channel"] == "B" and shop["connection_status"] != "authorized":
            return finish_recovery_task(connection, task["task_id"], "blocked", "SHOP_NOT_AUTHORIZED")
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
        order_count = connection.execute("SELECT COUNT(*) AS count FROM merchant.orders WHERE company_id = %s AND shop_id = %s AND external_order_id = %s", (event.company_id, event.shop_id, event.external_order_id)).fetchone()["count"]
        if order_count != 1:
            return finish_recovery_task(connection, task["task_id"], "failed", "ORDER_UNIQUENESS_FAILED")
        return finish_recovery_task(connection, task["task_id"], "completed", result={"event_id": event.event_id, "merchant_order_count": order_count})


def run_forever(poll_seconds: float = 0.5) -> None:
    recover_interrupted_tasks()
    while True:
        normal_result = process_next_task()
        recovery_result = process_next_recovery_task()
        if normal_result is None and recovery_result is None:
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
