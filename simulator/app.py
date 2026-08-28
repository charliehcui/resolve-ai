from datetime import datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="ResolveLab Simulator", version="0.1.0")


class CustomerAccount(BaseModel):
    customer_id: str
    status: str
    plan: str
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


customer_accounts = {
    "customer_001": CustomerAccount(
        customer_id="customer_001",
        status="active",
        plan="pro",
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
