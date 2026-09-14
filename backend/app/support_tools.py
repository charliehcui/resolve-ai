from typing import Literal
from urllib.parse import quote
from langchain_core.tools import tool

from app.resolvelab import get_resolvelab_data


@tool
def get_customer_account(customer_id: str) -> dict[str, object]:
    """Retrieve the current account details for a specific customer."""
    return get_resolvelab_data(f"/customers/{quote(customer_id, safe='')}")


@tool
def get_event_notification_deliveries(customer_id: str) -> list[dict[str, object]] | dict[str, object]:
    """Retrieve recent event notification delivery records for a specific customer."""
    return get_resolvelab_data(f"/customers/{quote(customer_id, safe='')}/event-notification-deliveries", expect_list=True)


@tool
def get_platform_status(service: Literal["event_notifications", "report_exports"] = "event_notifications") -> dict[str, object]:
    """Retrieve the current platform status for event notifications or report exports."""
    return get_resolvelab_data(f"/platform-status?service={service}")


@tool
def get_background_operation(customer_id: str) -> dict[str, object]:
    """Retrieve the latest report export operation, failure code, retry eligibility, and latest run status for a specific customer. This tool is read-only and never retries an operation."""
    return get_resolvelab_data(f"/customers/{quote(customer_id, safe='')}/background-operation")
