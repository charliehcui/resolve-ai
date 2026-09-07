import httpx

from app.core.config import settings


def get_current_product_context(customer_id: str) -> dict[str, object]:
    url = f"{settings.resolvelab_base_url}/customers/{customer_id}/product-context"
    response = httpx.get(url, timeout=5.0)
    response.raise_for_status()
    return response.json()


def get_recent_customer_activity(customer_id: str) -> dict[str, object]:
    url = f"{settings.resolvelab_base_url}/customers/{customer_id}/recent-activity"
    response = httpx.get(url, timeout=5.0)
    response.raise_for_status()
    return response.json()