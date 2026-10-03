"""Human-defined fixtures and independent database readback for the smoke suite."""
import json
import os
from pathlib import Path

import httpx
from psycopg import sql

from backend.app.database import get_connection
from simulator.lab import bootstrap, scenarios

SCENARIO_NAMES = set(scenarios.SCENARIOS) | {"documents", "order_missing", "order_sync_disabled", "order_unpaid", "order_cancelled", "order_normal", "missing_identifiers", "human_request", "customer_backend"}


def reset_case(directory: Path) -> dict[str, str]:
    from urllib.parse import urlsplit

    database_name = urlsplit(os.environ["DATABASE_URL"]).path.lstrip("/")
    if not database_name.startswith("resolveai_eval_"):
        raise PermissionError("Evaluation fixture reset is restricted to resolveai_eval_* databases")
    with get_connection() as connection:
        tables = connection.execute("SELECT schemaname, tablename FROM pg_tables WHERE schemaname IN ('platform', 'merchant', 'warehouse')").fetchall()
        identifiers = [sql.Identifier(row["schemaname"], row["tablename"]) for row in tables]
        identifiers.append(sql.Identifier("support", "conversations"))
        connection.execute(sql.SQL("TRUNCATE {} CASCADE").format(sql.SQL(", ").join(identifiers)))
    bootstrap.TOKEN_FILE = directory / "user_tokens.json"
    bootstrap.SERVICE_TOKEN_FILE = Path(os.environ["EVAL_SERVICE_TOKEN_FILE"])
    scenarios.USER_TOKEN_FILE = bootstrap.TOKEN_FILE
    bootstrap.bootstrap()
    tokens = json.loads(bootstrap.TOKEN_FILE.read_text(encoding="utf-8"))
    from backend.app.auth import hash_token

    tokens["staff-a-other"] = os.environ["EVAL_OTHER_USER_TOKEN"]
    with get_connection() as connection:
        connection.execute("INSERT INTO support.users (user_id, company_id, name, role) VALUES ('staff-a-other', 'company-a', 'Evaluation other user', 'staff') ON CONFLICT DO NOTHING")
        connection.execute("DELETE FROM support.access_tokens WHERE user_id = 'staff-a-other'")
        connection.execute("INSERT INTO support.access_tokens (token_hash, user_id) VALUES (%s, 'staff-a-other')", (hash_token(tokens["staff-a-other"]),))
    return tokens


def arm_fault(mode: str | None, path: str | None) -> None:
    response = httpx.post(os.environ["MERCHANT_URL"] + "/__eval__/arm", json={"mode": mode, "path": path}, timeout=5)
    response.raise_for_status()


def seed_case(case: dict) -> dict:
    name = case["scenario"]
    if name in scenarios.SCENARIOS:
        result = scenarios.seed_scenario(name)
        result["company_id"] = "company-a"
        if name == "shipment_response_lost":
            source = result["order"]["platform_order"]
            result["shop_id"] = source["shop_id"]
            result["order_id"] = source["external_order_id"]
        return result
    if name in {"documents", "missing_identifiers", "human_request", "customer_backend"}:
        return {"scenario": name, "company_id": "company-a", "shop_id": "shop-a", "order_id": "O-NOT-EXIST"}
    shop_id = "shop-a"
    order_id = "O-EVAL-" + case["case_id"].upper()
    payment = {"order_unpaid": "unpaid", "order_cancelled": "cancelled"}.get(name, "paid")
    if name == "order_missing":
        arm_fault("drop_before_accept", "/events/orders")
    if name == "order_sync_disabled":
        scenarios.set_shop_sync(shop_id, False)
    order = scenarios.create_order(shop_id, order_id, "SKU-1", 2, 20000, payment, wait_seconds=0.5 if name == "order_missing" else 8)
    return {"scenario": name, "company_id": "company-a", "shop_id": shop_id, "order_id": order_id, "order": order}


def business_snapshot(initial: dict) -> dict:
    company = initial["company_id"]
    shop = initial["shop_id"]
    order = initial.get("order_id", "")
    with get_connection() as connection:
        source = connection.execute("SELECT event_id::text, sku, quantity, amount_minor, payment_status, version FROM platform.orders WHERE company_id = %s AND shop_id = %s AND external_order_id = %s", (company, shop, order)).fetchone()
        orders = connection.execute("SELECT event_id::text, merchant_sku, quantity, amount_minor FROM merchant.orders WHERE company_id = %s AND shop_id = %s AND external_order_id = %s", (company, shop, order)).fetchall()
        shipments = connection.execute("SELECT shipment_id::text, carrier, tracking_number FROM platform.shipments WHERE company_id = %s AND shop_id = %s AND external_order_id = %s", (company, shop, order)).fetchall()
        warehouse = connection.execute("SELECT shipment_id::text, carrier, tracking_number FROM warehouse.shipments WHERE company_id = %s AND shop_id = %s AND external_order_id = %s", (company, shop, order)).fetchall()
        mapping = connection.execute("SELECT merchant_sku FROM merchant.sku_mappings WHERE company_id = %s AND shop_id = %s AND platform_sku = %s AND active", (company, shop, source["sku"] if source else "SKU-1")).fetchone()
        stock = connection.execute("""SELECT w.physical_quantity, w.reserved_quantity, w.version AS warehouse_version, r.safety_stock, p.quantity, p.source_version
            FROM merchant.stock_rules r JOIN warehouse.stock_items w ON w.company_id = r.company_id AND w.warehouse_sku = r.warehouse_sku
            LEFT JOIN platform.stock_levels p ON p.company_id = r.company_id AND p.shop_id = r.shop_id AND p.platform_sku = r.platform_sku
            WHERE r.company_id = %s AND r.shop_id = %s AND r.platform_sku = 'SKU-1'""", (company, shop)).fetchone()
        counts = {}
        for table in ("merchant.orders", "merchant.order_repair_receipts", "merchant.shipment_repair_receipts", "merchant.action_repair_receipts", "platform.shipments", "warehouse.shipments"):
            schema, table_name = table.split(".")
            counts[table] = connection.execute(sql.SQL("SELECT COUNT(*) AS count FROM {}").format(sql.Identifier(schema, table_name))).fetchone()["count"]
        business_rows = {}
        for table in ("platform.orders", "platform.shipments", "platform.stock_levels", "merchant.shops", "merchant.sku_mappings", "merchant.stock_rules", "merchant.orders", "warehouse.orders", "warehouse.shipments", "warehouse.stock_items"):
            schema, table_name = table.split(".")
            rows = connection.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(schema, table_name))).fetchall()
            business_rows[table] = sorted([dict(row) for row in rows], key=lambda row: json.dumps(row, sort_keys=True, default=str))
        shop_state = connection.execute("SELECT sync_enabled, shipment_sync_enabled, connection_status, version FROM merchant.shops WHERE company_id = %s AND shop_id = %s", (company, shop)).fetchone()
        tasks = connection.execute("SELECT status, error_code, attempts FROM merchant.order_tasks WHERE company_id = %s AND shop_id = %s AND external_order_id = %s", (company, shop, order)).fetchall()
    order_correct = bool(source and len(orders) == 1 and mapping and source["payment_status"] == "paid" and orders[0]["event_id"] == source["event_id"] and orders[0]["merchant_sku"] == mapping["merchant_sku"] and orders[0]["quantity"] == source["quantity"] and orders[0]["amount_minor"] == source["amount_minor"])
    shipment_correct = bool(len(shipments) == len(warehouse) == 1 and shipments[0] == warehouse[0])
    stock_correct = bool(stock and stock["source_version"] == stock["warehouse_version"] and stock["quantity"] == max(stock["physical_quantity"] - stock["reserved_quantity"] - stock["safety_stock"], 0))
    return {"source": source, "orders": orders, "shipments": shipments, "warehouse_shipments": warehouse, "stock": stock, "shop": shop_state, "tasks": tasks, "counts": counts, "business_rows": business_rows, "order_correct": order_correct, "shipment_correct": shipment_correct, "stock_correct": stock_correct, "order_count": len(orders), "shipment_count": len(shipments)}


def mutate_facts(operation: str, initial: dict, action_id: str) -> dict | None:
    changes = {
        "expired": ("UPDATE support.action_proposals SET expires_at = NOW() - INTERVAL '1 second' WHERE action_id = %s", (action_id,)),
        "source_changed": ("UPDATE platform.orders SET quantity = quantity + 1, version = version + 1 WHERE company_id = %s AND shop_id = %s AND external_order_id = %s", (initial["company_id"], initial["shop_id"], initial.get("order_id", ""))),
        "shop_changed": ("UPDATE merchant.shops SET version = version + 1 WHERE company_id = %s AND shop_id = %s", (initial["company_id"], initial["shop_id"])),
        "missing_decision": ("UPDATE support.action_proposals SET status = 'approved' WHERE action_id = %s", (action_id,)),
        "rejected": None,
    }
    change = changes.get(operation)
    if change:
        with get_connection() as connection:
            connection.execute(*change)
        return {"operation": operation, "sql": change[0], "parameters": change[1]}
    return None
