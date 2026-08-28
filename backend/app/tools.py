import httpx
from langchain_core.tools import tool

from app.core.config import settings


@tool
def get_customer_account(customer_id: str) -> dict[str, object]:
    """Get the current account information for a customer."""
    response = httpx.get(f"{settings.resolvelab_base_url}/customers/{customer_id}", timeout=5.0)
    response.raise_for_status()
    return response.json()


@tool
def get_event_notification_deliveries(customer_id: str) -> list[dict[str, object]]:
    """Get recent event notification deliveries for a customer."""
    url = f"{settings.resolvelab_base_url}/customers/{customer_id}/event-notification-deliveries"
    response = httpx.get(url, timeout=5.0)
    response.raise_for_status()
    return response.json()


@tool
def get_platform_status() -> dict[str, object]:
    """Get the current event notification platform status."""
    response = httpx.get(f"{settings.resolvelab_base_url}/platform-status", timeout=5.0)
    response.raise_for_status()
    return response.json()
