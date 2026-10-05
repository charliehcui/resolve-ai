"""Human-defined fixtures and independent database readback for the smoke suite."""
import json
import os
import time
from pathlib import Path
from uuid import uuid4

import httpx
from psycopg import sql

from backend.app.database import get_connection
from simulator.lab import bootstrap, scenarios

SCENARIO_NAMES = set(scenarios.SCENARIOS) | {"documents", "order_missing", "order_sync_disabled", "order_unpaid", "order_cancelled", "order_normal", "missing_identifiers", "human_request", "customer_backend", "stock_missing_source", "merchant_read_failure", "shipment_not_dispatched", "stock_consistent", "stock_newer_platform"}
SCENARIO_NAMES |= {"missing_order_identifier", "order_auth_restored", "shipment_accepted_response_lost", "stock_rule_missing", "completed_order_source_changed", "shipment_tracking_conflict", "stock_publish_connection_restored", "order_sync_restored_read_failure"}
SCENARIO_NAMES |= {"source_order_absent", "stock_zero_floor", "platform_shipment_without_dispatch"}
SCENARIO_NAMES |= {"missing_shop_identifier", "order_connection_forbidden", "order_channel_timeout", "stock_same_version_mismatch", "shipment_sync_disabled", "completed_order_auth_expired", "order_and_stock_mapping_missing", "shipment_failure_worker_completed", "outage_process_read_failure", "stock_difference_auth_expired", "source_conflict_declined_handoff", "restored_auth_sync_disabled", "shipment_process_read_failure", "missing_receipt_declined_retry", "stock_publish_in_progress"}


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
    from hashlib import sha256

    tokens["staff-a-other"] = os.environ["EVAL_OTHER_USER_TOKEN"]
    with get_connection() as connection:
        connection.execute("INSERT INTO support.users (user_id, company_id, name, role) VALUES ('staff-a-other', 'company-a', 'Evaluation other user', 'staff') ON CONFLICT DO NOTHING")
        connection.execute("DELETE FROM support.access_tokens WHERE user_id = 'staff-a-other'")
        connection.execute("INSERT INTO support.access_tokens (token_hash, user_id) VALUES (%s, 'staff-a-other')", (sha256(tokens["staff-a-other"].encode("utf-8")).hexdigest(),))
    return tokens


def arm_fault(mode: str | None, path: str | None) -> None:
    response = httpx.post(os.environ["MERCHANT_URL"] + "/__eval__/arm", json={"mode": mode, "path": path}, timeout=5)
    response.raise_for_status()


def seed_case(case: dict) -> dict:
    name = case["scenario"]
    if name == "missing_shop_identifier":
        return {"scenario": "missing_identifiers", "company_id": "company-a", "shop_id": "shop-a", "order_id": "O-UNKNOWN-SHOP"}
    combined = {
        "stock_same_version_mismatch": "stock_consistent",
        "shipment_sync_disabled": "order_normal",
        "completed_order_auth_expired": "order_normal",
        "order_and_stock_mapping_missing": "missing_sku_mapping",
        "shipment_failure_worker_completed": "shipment_sync_failure",
        "outage_process_read_failure": "third_party_outage",
        "stock_difference_auth_expired": "inventory_mismatch",
        "source_conflict_declined_handoff": "completed_order_source_changed",
        "restored_auth_sync_disabled": "order_auth_restored",
        "shipment_process_read_failure": "shipment_sync_failure",
        "missing_receipt_declined_retry": "order_missing",
        "stock_publish_in_progress": "inventory_mismatch",
    }
    if name in combined:
        initial = seed_case(dict(case, scenario=combined[name]))
        initial["scenario"] = name
        shop_id = initial["shop_id"]
        if name == "stock_same_version_mismatch":
            with get_connection() as connection:
                connection.execute("UPDATE platform.stock_levels SET quantity = quantity + 1 WHERE company_id = %s AND shop_id = %s AND platform_sku = %s", (initial["company_id"], shop_id, initial["sku"]))
        elif name == "shipment_sync_disabled":
            scenarios.set_shipment_sync(shop_id, False)
            initial["shipment"] = scenarios.create_shipment(shop_id, initial["order_id"], "test-express", "TRACK-" + uuid4().hex[:8])
            assert initial["shipment"]["merchant"]["error_code"] == "SHIPMENT_SYNC_DISABLED" and not initial["shipment"]["platform"]
        elif name in {"completed_order_auth_expired", "stock_difference_auth_expired"}:
            scenarios.set_connection(shop_id, "auth_expired")
        elif name == "order_and_stock_mapping_missing":
            initial["sku"] = "SKU-UNKNOWN"
            initial["message"] += " 同时核对 SKU-UNKNOWN 的库存。"
        elif name == "restored_auth_sync_disabled":
            scenarios.set_shop_sync(shop_id, False)
        elif name in {"outage_process_read_failure", "shipment_process_read_failure"}:
            endpoint = "process-records" if name == "outage_process_read_failure" else "shipment-records"
            arm_fault("read_unavailable", f"/internal/{endpoint}/{initial['order_id']}?shop_id={shop_id}")
        elif name == "stock_publish_in_progress":
            # Freeze an in-flight publication. The worker selects pending tasks only.
            with get_connection() as connection:
                connection.execute("UPDATE merchant.stock_publish_tasks SET status = 'processing', updated_at = NOW() - INTERVAL '2 minutes' WHERE company_id = %s AND shop_id = %s AND platform_sku = %s", (initial["company_id"], shop_id, initial["sku"]))
        return initial
    if name == "source_order_absent":
        initial = {"scenario": name, "company_id": "company-a", "shop_id": "shop-a", "order_id": "O-EVAL-ABSENT"}
        with get_connection() as connection:
            for table in ("platform.orders", "merchant.orders", "merchant.order_tasks"):
                count = connection.execute(sql.SQL("SELECT COUNT(*) AS count FROM {} WHERE company_id = %s AND shop_id = %s AND external_order_id = %s").format(sql.Identifier(*table.split("."))), (initial["company_id"], initial["shop_id"], initial["order_id"])).fetchone()["count"]
                assert count == 0
        initial["message"] = "shop-a 的订单 O-EVAL-ABSENT 需要核对。"
        return initial
    if name == "stock_zero_floor":
        publication = scenarios.publish_stock("shop-a", "SKU-1", "MERCHANT-SKU-1", 5, 10)
        assert publication["merchant"]["task"]["task_status"] == "completed"
        assert publication["platform"]["quantity"] == 0 and publication["platform"]["source_version"] == publication["warehouse"]["version"]
        assert publication["merchant"]["rule"]["safety_stock"] == 5
        return {"scenario": name, "company_id": "company-a", "shop_id": "shop-a", "sku": "SKU-1", "publication": publication, "message": "shop-a 的 SKU-1 库存需要核对。"}
    if name == "platform_shipment_without_dispatch":
        initial = seed_case(dict(case, scenario="order_normal"))
        initial["scenario"] = name
        from datetime import UTC, datetime

        payload = {"request_id": str(uuid4()), "shipment_id": str(uuid4()), "company_id": initial["company_id"], "shop_id": initial["shop_id"], "external_order_id": initial["order_id"], "carrier": "test-express", "tracking_number": "EXTERNAL-TRACK", "version": 1, "shipped_at": datetime.now(UTC).isoformat()}
        response = httpx.post(scenarios.urls()[0] + "/internal/shipments", json=payload, headers={"X-Service-Token": scenarios.read_service_token("platform-shipment")}, timeout=5)
        response.raise_for_status()
        assert response.json()["accepted"] is True
        snapshot = business_snapshot(initial)
        assert len(snapshot["shipments"]) == 1 and not snapshot["warehouse_shipments"]
        assert snapshot["business_rows"]["warehouse.orders"][0]["status"] == "awaiting_shipment"
        initial["platform_shipment"] = response.json()
        return initial
    if name == "missing_order_identifier":
        return {"scenario": "missing_identifiers", "company_id": "company-a", "shop_id": "shop-a"}
    if name in {"order_auth_restored", "order_sync_restored_read_failure"}:
        shop_id = "shop-a"
        order_id = "O-EVAL-" + case["case_id"].upper()
        if name == "order_auth_restored":
            scenarios.set_connection(shop_id, "auth_expired")
        else:
            scenarios.set_shop_sync(shop_id, False)
        order = scenarios.create_order(shop_id, order_id, "SKU-1", 2, 20000)
        expected_error = "CHANNEL_AUTH_EXPIRED" if name == "order_auth_restored" else "ORDER_SYNC_DISABLED"
        assert order["task"]["status"] == "blocked" and order["task"]["error_code"] == expected_error and not order["merchant_order"]
        if name == "order_auth_restored":
            assert scenarios.restore_connection(shop_id)["connection_status"] == "authorized"
        else:
            scenarios.set_shop_sync(shop_id, True)
            arm_fault("read_unavailable", f"/internal/shops/{shop_id}/status")
        return {"scenario": name, "company_id": "company-a", "shop_id": shop_id, "order_id": order_id, "order": order, "message": f"{shop_id} 的订单 {order_id} 需要调查。"}
    if name in {"shipment_accepted_response_lost", "shipment_tracking_conflict", "completed_order_source_changed"}:
        initial = seed_case(dict(case, scenario="order_normal"))
        initial["scenario"] = name
        shop_id, order_id = initial["shop_id"], initial["order_id"]
        if name == "completed_order_source_changed":
            with get_connection() as connection:
                connection.execute("UPDATE platform.orders SET quantity = quantity + 1, version = version + 1 WHERE company_id = %s AND shop_id = %s AND external_order_id = %s", (initial["company_id"], shop_id, order_id))
                row = connection.execute("SELECT p.quantity AS platform_quantity, m.quantity AS merchant_quantity FROM platform.orders p JOIN merchant.orders m USING (company_id, shop_id, external_order_id) WHERE p.shop_id = %s AND p.external_order_id = %s", (shop_id, order_id)).fetchone()
                assert row["platform_quantity"] != row["merchant_quantity"]
            return initial
        if name == "shipment_accepted_response_lost":
            scenarios.arm_shipment_response_lost(shop_id)
        shipment = scenarios.create_shipment(shop_id, order_id, "test-express", "TRACK-" + uuid4().hex[:8])
        assert shipment["platform"] and shipment["warehouse"] and shipment["platform"]["tracking_number"] == shipment["warehouse"]["tracking_number"]
        if name == "shipment_accepted_response_lost":
            # The real worker may reconcile the accepted shipment before this read.
            assert shipment["merchant"]["task_status"] in {"unknown", "completed"}
            if shipment["merchant"]["task_status"] == "unknown":
                assert shipment["merchant"]["error_code"] == "PLATFORM_RESPONSE_LOST"
        else:
            with get_connection() as connection:
                connection.execute("UPDATE platform.shipments SET tracking_number = %s, version = version + 1 WHERE company_id = %s AND shop_id = %s AND external_order_id = %s", ("TRACK-CONFLICT", initial["company_id"], shop_id, order_id))
                row = connection.execute("SELECT p.tracking_number AS platform_tracking, w.tracking_number AS warehouse_tracking FROM platform.shipments p JOIN warehouse.shipments w USING (company_id, shop_id, external_order_id) WHERE p.shop_id = %s AND p.external_order_id = %s", (shop_id, order_id)).fetchone()
                assert row["platform_tracking"] != row["warehouse_tracking"]
        initial["shipment"] = shipment
        return initial
    if name in {"stock_rule_missing", "stock_publish_connection_restored"}:
        initial = seed_case(dict(case, scenario="stock_consistent"))
        initial["scenario"] = name
        if name == "stock_rule_missing":
            with get_connection() as connection:
                connection.execute("UPDATE merchant.stock_rules SET active = FALSE WHERE company_id = %s AND shop_id = %s AND platform_sku = %s", (initial["company_id"], initial["shop_id"], initial["sku"]))
                assert connection.execute("SELECT COUNT(*) AS count FROM merchant.stock_rules WHERE active AND company_id = %s AND shop_id = %s AND platform_sku = %s", (initial["company_id"], initial["shop_id"], initial["sku"])).fetchone()["count"] == 0
            return initial
        scenarios.set_connection(initial["shop_id"], "unavailable")
        publication = scenarios.publish_stock(initial["shop_id"], initial["sku"], "MERCHANT-SKU-1", 90, 10)
        assert publication["merchant"]["task"]["task_status"] == "failed" and publication["merchant"]["task"]["error_code"] == "CHANNEL_UNAVAILABLE"
        assert scenarios.restore_connection(initial["shop_id"])["connection_status"] == "authorized"
        response = httpx.post(scenarios.warehouse_url() + "/lab/stocks/MERCHANT-SKU-1", params={"company_id": initial["company_id"]}, json={"physical_quantity": 90, "reserved_quantity": 10, "observed_seconds_ago": 90}, headers={"X-Lab-Token": scenarios.read_service_token("lab-control")}, timeout=5)
        response.raise_for_status()
        assert publication["warehouse"]["version"] > publication["platform"]["source_version"]
        initial["publication"] = publication
        return initial
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
    if name in {"stock_missing_source", "stock_consistent", "stock_newer_platform"}:
        result = {"scenario": name, "company_id": "company-a", "shop_id": "shop-a", "sku": "SKU-1", "message": "shop-a 的 SKU-1 库存需要核对。"}
        if name == "stock_missing_source":
            with get_connection() as connection:
                assert connection.execute("SELECT COUNT(*) AS count FROM warehouse.stock_items").fetchone()["count"] == 0
            return result
        result["publish"] = scenarios.publish_stock("shop-a", "SKU-1", "MERCHANT-SKU-1", 80, 10)
        facts = result["publish"]
        assert facts["platform"]["quantity"] == 65 and facts["platform"]["source_version"] == facts["warehouse"]["version"]
        if name == "stock_newer_platform":
            response = httpx.post(scenarios.urls()[0] + "/internal/stocks", json={"request_id": str(uuid4()), "company_id": "company-a", "shop_id": "shop-a", "platform_sku": "SKU-1", "quantity": 75, "source_version": facts["warehouse"]["version"] + 1}, headers={"X-Service-Token": scenarios.read_service_token("platform-stock")}, timeout=5)
            response.raise_for_status()
            response = httpx.post(scenarios.warehouse_url() + "/lab/stocks/MERCHANT-SKU-1", params={"company_id": "company-a"}, json={"physical_quantity": 80, "reserved_quantity": 10, "observed_seconds_ago": 90}, headers={"X-Lab-Token": scenarios.read_service_token("lab-control")}, timeout=5)
            response.raise_for_status()
        return result
    shop_id = "shop-a"
    order_id = "O-EVAL-" + case["case_id"].upper()
    payment = {"order_unpaid": "unpaid", "order_cancelled": "cancelled"}.get(name, "paid")
    if name == "order_missing":
        arm_fault("drop_before_accept", "/events/orders")
    if name == "order_sync_disabled":
        scenarios.set_shop_sync(shop_id, False)
    if name in {"order_connection_forbidden", "order_channel_timeout"}:
        scenarios.set_connection(shop_id, "forbidden" if name == "order_connection_forbidden" else "timeout")
    order = scenarios.create_order(shop_id, order_id, "SKU-1", 2, 20000, payment, wait_seconds=0.5 if name == "order_missing" else 8)
    if name in {"order_normal", "merchant_read_failure", "shipment_not_dispatched"}:
        assert order["task"]["status"] == "completed" and order["merchant_order"]
        deadline = time.monotonic() + 8
        while True:
            response = httpx.get(scenarios.warehouse_url() + f"/orders/{order_id}", params={"shop_id": shop_id}, headers=scenarios.user_headers(), timeout=5)
            if response.is_success:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Workflow fixture warehouse dispatch did not complete")
            time.sleep(0.25)
    if name == "merchant_read_failure":
        arm_fault("read_unavailable", f"/internal/process-records/{order_id}?shop_id={shop_id}")
    if name == "order_unpaid":
        assert order["task"]["error_code"] == "ORDER_NOT_PAID" and not order["merchant_order"]
    if name == "order_cancelled":
        assert order["platform_order"]["payment_status"] == "cancelled" and order["task"]["error_code"] == "ORDER_NOT_PAID" and not order["merchant_order"]
    if name == "order_missing":
        assert order["platform_order"] and not order["task"] and not order["merchant_order"]
    return {"scenario": name, "company_id": "company-a", "shop_id": shop_id, "order_id": order_id, "order": order, "message": f"{shop_id} 的订单 {order_id} 需要核对。"}


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
