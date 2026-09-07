from datetime import datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="ResolveLab Simulator", version="0.1.0")


class CustomerAccount(BaseModel):
    customer_id: str
    status: str
    plan: str
    product_version: str
    event_notifications_enabled: bool
    updated_at: datetime


class EventNotificationDelivery(BaseModel):
    delivery_id: str
    customer_id: str
    order_id: str
    notification_type: str
    delivery_status: str
    response_status: int
    response_message: str
    attempted_at: datetime


class PlatformStatus(BaseModel):
    service: str
    status: str
    updated_at: datetime


class CustomerProductContext(BaseModel):
    account_status: str
    product_version: str
    affected_feature: str
    feature_enabled: bool


class CustomerRecentActivity(BaseModel):
    affected_feature: str
    activity: str
    result: str
    occurred_at: datetime


customer_accounts = {
    "customer_001": CustomerAccount(
        customer_id="customer_001",
        status="active",
        plan="pro",
        product_version="2026.8",
        event_notifications_enabled=True,
        updated_at="2026-08-25T09:00:00Z",
    )
}

event_notification_deliveries = [
    EventNotificationDelivery(
        delivery_id="delivery_001",
        customer_id="customer_001",
        order_id="order_1001",
        notification_type="order_completed",
        delivery_status="failed",
        response_status=401,
        response_message="Unauthorized",
        attempted_at="2026-08-25T09:15:00Z",
    ),
    EventNotificationDelivery(
        delivery_id="delivery_002",
        customer_id="customer_001",
        order_id="order_1001",
        notification_type="order_completed",
        delivery_status="failed",
        response_status=401,
        response_message="Unauthorized",
        attempted_at="2026-08-25T09:20:00Z",
    ),
]

platform_status = PlatformStatus(
    service="event_notifications",
    status="operational",
    updated_at="2026-08-25T09:25:00Z",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/customers/{customer_id}", response_model=CustomerAccount)
def get_customer_account(customer_id: str) -> CustomerAccount:
    customer = customer_accounts.get(customer_id)

    if customer is None:
        raise HTTPException(status_code=404, detail="Customer account not found")

    return customer


@app.get("/customers/{customer_id}/product-context", response_model=CustomerProductContext)
def get_customer_product_context(customer_id: str) -> CustomerProductContext:
    customer = customer_accounts.get(customer_id)

    if customer is None:
        raise HTTPException(status_code=404, detail="Customer account not found")

    return CustomerProductContext(
        account_status=customer.status,
        product_version=customer.product_version,
        affected_feature="event notifications",
        feature_enabled=customer.event_notifications_enabled,
    )


@app.get("/customers/{customer_id}/recent-activity", response_model=CustomerRecentActivity)
def get_customer_recent_activity(customer_id: str) -> CustomerRecentActivity:
    recent_delivery = None

    for delivery in event_notification_deliveries:
        if delivery.customer_id == customer_id:
            if recent_delivery is None or delivery.attempted_at > recent_delivery.attempted_at:
                recent_delivery = delivery

    if recent_delivery is None:
        raise HTTPException(status_code=404, detail="Recent customer activity not found")

    return CustomerRecentActivity(
        affected_feature="event notifications",
        activity="Sending the latest order notification",
        result=recent_delivery.delivery_status,
        occurred_at=recent_delivery.attempted_at,
    )


@app.get("/customers/{customer_id}/event-notification-deliveries", response_model=list[EventNotificationDelivery])
def get_event_notification_deliveries(customer_id: str) -> list[EventNotificationDelivery]:
    customer_deliveries = []

    for delivery in event_notification_deliveries:
        if delivery.customer_id == customer_id:
            customer_deliveries.append(delivery)

    if not customer_deliveries:
        raise HTTPException(status_code=404, detail="Customer event notification deliveries not found")

    return customer_deliveries


@app.get("/platform-status", response_model=PlatformStatus)
def get_platform_status() -> PlatformStatus:
    return platform_status