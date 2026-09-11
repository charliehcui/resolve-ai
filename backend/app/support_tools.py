import httpx
from langchain_core.tools import tool

from app.core.config import settings


@tool
def get_customer_account(customer_id: str) -> dict[str, object]:
    """Retrieve the current account details for a specific customer."""
    response = httpx.get(f"{settings.resolvelab_base_url}/customers/{customer_id}", timeout=5.0)
    response.raise_for_status()
    return response.json()


@tool
def get_event_notification_deliveries(customer_id: str) -> list[dict[str, object]]:
    """Retrieve recent event notification delivery records for a specific customer."""
    url = f"{settings.resolvelab_base_url}/customers/{customer_id}/event-notification-deliveries"
    response = httpx.get(url, timeout=5.0)
    response.raise_for_status()
    return response.json()


@tool
def get_platform_status() -> dict[str, object]:
    """Retrieve the current event notification platform status."""
    response = httpx.get(f"{settings.resolvelab_base_url}/platform-status", timeout=5.0)
    response.raise_for_status()
    return response.json()
