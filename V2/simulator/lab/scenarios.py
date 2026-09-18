import json
import os
import time

import httpx

from backend.app.config import PROJECT_ROOT
from simulator.services.common import read_service_token

USER_TOKEN_FILE = PROJECT_ROOT / ".local" / "test_tokens.json"


def user_token(user_id: str = "admin-a") -> str:
    return str(json.loads(USER_TOKEN_FILE.read_text(encoding="utf-8"))[user_id])


def user_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {user_token()}"}


def urls() -> tuple[str, str]:
    return os.getenv("PLATFORM_URL", "http://127.0.0.1:8001"), os.getenv("MERCHANT_URL", "http://127.0.0.1:8002")


def warehouse_url() -> str:
    return os.getenv("WAREHOUSE_URL", "http://127.0.0.1:8003")


def set_shop_sync(shop_id: str, enabled: bool) -> dict[str, object]:
    _, merchant_url = urls()
    response = httpx.post(f"{merchant_url}/lab/shops/{shop_id}/sync", params={"company_id": "company-a"}, json={"enabled": enabled}, headers={"X-Lab-Token": read_service_token("lab-control")}, timeout=5)
    response.raise_for_status()
    return response.json()


def set_shipment_sync(shop_id: str, enabled: bool) -> dict[str, object]:
    _, merchant_url = urls()
    response = httpx.post(f"{merchant_url}/lab/shops/{shop_id}/shipment-sync", params={"company_id": "company-a"}, json={"enabled": enabled}, headers={"X-Lab-Token": read_service_token("lab-control")}, timeout=5)
    response.raise_for_status()
    return response.json()


def set_connection(shop_id: str, status: str) -> dict[str, object]:
    _, merchant_url = urls()
    response = httpx.post(f"{merchant_url}/lab/shops/{shop_id}/connection", params={"company_id": "company-a"}, json={"status": status}, headers={"X-Lab-Token": read_service_token("lab-control")}, timeout=5)
    response.raise_for_status()
    return response.json()


def restore_connection(shop_id: str) -> dict[str, object]:
    _, merchant_url = urls()
    response = httpx.post(f"{merchant_url}/lab/shops/{shop_id}/connection/restore", params={"company_id": "company-a"}, headers={"X-Lab-Token": read_service_token("lab-control")}, timeout=5)
    response.raise_for_status()
    return response.json()


def publish_stock(shop_id: str, platform_sku: str, warehouse_sku: str, physical_quantity: int, reserved_quantity: int, wait_seconds: float = 8) -> dict[str, object]:
    _, merchant_url = urls()
    warehouse_response = httpx.post(f"{warehouse_url()}/lab/stocks/{warehouse_sku}", params={"company_id": "company-a"}, json={"physical_quantity": physical_quantity, "reserved_quantity": reserved_quantity}, headers={"X-Lab-Token": read_service_token("lab-control")}, timeout=5)
    warehouse_response.raise_for_status()
    publish_response = httpx.post(f"{merchant_url}/stocks/publish", json={"shop_id": shop_id, "platform_sku": platform_sku}, headers=user_headers(), timeout=5)
    publish_response.raise_for_status()
    result = publish_response.json()
    deadline = time.monotonic() + wait_seconds
    facts = stock_facts(shop_id, platform_sku, warehouse_sku)
    while time.monotonic() < deadline:
        facts = stock_facts(shop_id, platform_sku, warehouse_sku)
        task = (facts.get("merchant") or {}).get("task") or {}
        if task.get("task_status") in {"completed", "failed", "blocked"}:
            break
        time.sleep(0.25)
    return {"warehouse_update": warehouse_response.json(), "publish_task": result, **facts}


def stock_facts(shop_id: str, platform_sku: str, warehouse_sku: str) -> dict[str, object]:
    platform_url, merchant_url = urls()
    headers = {"X-Service-Token": read_service_token("support-read"), "X-Company-ID": "company-a"}
    responses = {
        "warehouse": httpx.get(f"{warehouse_url()}/internal/stocks/{warehouse_sku}", headers=headers, timeout=5),
        "merchant": httpx.get(f"{merchant_url}/internal/stock-records/{platform_sku}", params={"shop_id": shop_id}, headers=headers, timeout=5),
        "platform": httpx.get(f"{platform_url}/internal/stocks/{platform_sku}", params={"shop_id": shop_id}, headers=headers, timeout=5),
    }
    return {name: response.json() if response.is_success else None for name, response in responses.items()}


def arm_shipment_response_lost(shop_id: str, delay_seconds: float = 6) -> dict[str, object]:
    platform_url, _ = urls()
    response = httpx.post(f"{platform_url}/lab/shops/{shop_id}/shipment-response-lost", params={"company_id": "company-a", "delay_seconds": delay_seconds}, headers={"X-Lab-Token": read_service_token("lab-control")}, timeout=5)
    response.raise_for_status()
    return response.json()


def dispatch_order(shop_id: str, external_order_id: str) -> dict[str, object]:
    _, merchant_url = urls()
    response = httpx.post(f"{merchant_url}/orders/{external_order_id}/dispatch", params={"shop_id": shop_id}, headers=user_headers(), timeout=5)
    response.raise_for_status()
    return response.json()


def shipment_facts(shop_id: str, external_order_id: str) -> dict[str, object]:
    platform_url, merchant_url = urls()
    responses = {
        "warehouse": httpx.get(f"{warehouse_url()}/orders/{external_order_id}", params={"shop_id": shop_id}, headers=user_headers(), timeout=5),
        "merchant": httpx.get(f"{merchant_url}/internal/shipment-records/{external_order_id}", params={"shop_id": shop_id}, headers={"X-Service-Token": read_service_token("support-read"), "X-Company-ID": "company-a"}, timeout=5),
        "platform": httpx.get(f"{platform_url}/internal/shipments/{external_order_id}", params={"shop_id": shop_id}, headers={"X-Service-Token": read_service_token("support-read"), "X-Company-ID": "company-a"}, timeout=5),
    }
    return {name: response.json() if response.is_success else None for name, response in responses.items()}


def create_shipment(shop_id: str, external_order_id: str, carrier: str, tracking_number: str, wait_seconds: float = 8) -> dict[str, object]:
    response = httpx.post(f"{warehouse_url()}/shipments", json={"shop_id": shop_id, "external_order_id": external_order_id, "carrier": carrier, "tracking_number": tracking_number}, headers=user_headers(), timeout=8)
    response.raise_for_status()
    result = response.json()
    facts = shipment_facts(shop_id, external_order_id)
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        facts = shipment_facts(shop_id, external_order_id)
        merchant = facts.get("merchant") or {}
        if merchant.get("task_status") in {"completed", "blocked", "failed", "unknown"}:
            break
        time.sleep(0.25)
    return {"shipment": result, **facts}


def order_facts(shop_id: str, external_order_id: str, event_id: str) -> dict[str, object]:
    platform_url, merchant_url = urls()
    platform_response = httpx.get(f"{platform_url}/orders/{external_order_id}", params={"shop_id": shop_id}, headers=user_headers(), timeout=5)
    task_response = httpx.get(f"{merchant_url}/tasks/{event_id}", headers=user_headers(), timeout=5)
    merchant_response = httpx.get(f"{merchant_url}/orders/{external_order_id}", params={"shop_id": shop_id}, headers=user_headers(), timeout=5)
    return {
        "platform_order": platform_response.json() if platform_response.is_success else None,
        "task": task_response.json() if task_response.is_success else None,
        "merchant_order": merchant_response.json() if merchant_response.is_success else None,
    }


def create_order(shop_id: str, external_order_id: str, sku: str, quantity: int, amount_minor: int, payment_status: str = "paid", wait_seconds: float = 8) -> dict[str, object]:
    platform_url, _ = urls()
    response = httpx.post(
        f"{platform_url}/orders",
        json={"shop_id": shop_id, "external_order_id": external_order_id, "sku": sku, "quantity": quantity, "amount_minor": amount_minor, "payment_status": payment_status},
        headers=user_headers(),
        timeout=8,
    )
    response.raise_for_status()
    delivery = response.json()
    facts = {"platform_order": None, "task": None, "merchant_order": None}
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        facts = order_facts(shop_id, external_order_id, delivery["event_id"])
        if facts["task"] and facts["task"]["status"] in {"completed", "blocked", "failed"}:
            break
        time.sleep(0.25)
    return {"delivery": delivery, **facts}


def seed_scenario(name: str) -> dict[str, object]:
    if name != "shipment_response_lost":
        raise ValueError(f"Unknown scenario: {name}")
    shop_id = "shop-a"
    order_id = "O-SHIPMENT-RESPONSE-LOST"
    set_shipment_sync(shop_id, False)
    order = create_order(shop_id, order_id, "SKU-1", 1, 1000)
    dispatch_order(shop_id, order_id)
    shipment = create_shipment(shop_id, order_id, "test-express", "RESPONSE-LOST-1")
    if shipment.get("platform") is not None:
        raise RuntimeError("Scenario requires the platform shipment to remain absent before approval")
    control = arm_shipment_response_lost(shop_id)
    return {"scenario": name, "order": order, "shipment": shipment, "response_lost_control": control}
