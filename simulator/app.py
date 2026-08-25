from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(
    title="ResolveLab Simulator",
    version="0.1.0",
)


class NotificationAttempt(BaseModel):
    attempt_id: str
    customer_id: str
    order_id: str
    notification_type: str
    delivery_status: str
    response_status: int
    response_message: str
    attempted_at: str


notification_attempts = [
    NotificationAttempt(
        attempt_id="attempt_001",
        customer_id="customer_001",
        order_id="order_1001",
        notification_type="order_completed",
        delivery_status="failed",
        response_status=401,
        response_message="Unauthorized",
        attempted_at="2026-08-25T09:15:00Z",
    ),
    NotificationAttempt(
        attempt_id="attempt_002",
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


@app.get(
    "/customers/{customer_id}/notification-attempts",
    response_model=list[NotificationAttempt],
)
def get_notification_attempts(customer_id: str) -> list[NotificationAttempt]:
    customer_attempts = []

    for attempt in notification_attempts:
        if attempt.customer_id == customer_id:
            customer_attempts.append(attempt)

    if not customer_attempts:
        raise HTTPException(status_code=404, detail="Customer notification attempts not found")

    return customer_attempts