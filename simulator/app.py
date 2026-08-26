from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(
    title="ResolveLab Simulator",
    version="0.1.0",
)


class EventNotificationDelivery(BaseModel):
    delivery_id: str
    customer_id: str
    order_id: str
    notification_type: str
    delivery_status: str
    response_status: int
    response_message: str
    attempted_at: str


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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/customers/{customer_id}/event-notification-deliveries", response_model=list[EventNotificationDelivery])
def get_event_notification_deliveries(customer_id: str) -> list[EventNotificationDelivery]:
    customer_deliveries = []

    for delivery in event_notification_deliveries:
        if delivery.customer_id == customer_id:
            customer_deliveries.append(delivery)

    if not customer_deliveries:
        raise HTTPException(status_code=404, detail="Customer event notification deliveries not found")

    return customer_deliveries
