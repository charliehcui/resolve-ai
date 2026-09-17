import json
import os
import time

import httpx

from app.config import PROJECT_ROOT
from services.common import read_service_token

USER_TOKEN_FILE = PROJECT_ROOT / ".local" / "test_tokens.json"


def user_token(user_id: str = "admin-a") -> str:
    return str(json.loads(USER_TOKEN_FILE.read_text(encoding="utf-8"))[user_id])


def user_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {user_token()}"}


def urls() -> tuple[str, str]:
    return os.getenv("PLATFORM_URL", "http://127.0.0.1:8001"), os.getenv("MERCHANT_URL", "http://127.0.0.1:8002")


def set_shop_sync(shop_id: str, enabled: bool) -> dict[str, object]:
    _, merchant_url = urls()
    response = httpx.post(f"{merchant_url}/lab/shops/{shop_id}/sync", params={"company_id": "company-a"}, json={"enabled": enabled}, headers={"X-Lab-Token": read_service_token("lab-control")}, timeout=5)
    response.raise_for_status()
    return response.json()


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
