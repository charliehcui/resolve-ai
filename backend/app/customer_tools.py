from urllib.parse import quote

from app.resolvelab import get_resolvelab_data


def get_current_product_context(customer_id: str) -> dict[str, object]:
    return get_resolvelab_data(f"/customers/{quote(customer_id, safe='')}/product-context")


def get_recent_customer_activity(customer_id: str) -> dict[str, object]:
    return get_resolvelab_data(f"/customers/{quote(customer_id, safe='')}/recent-activity")
