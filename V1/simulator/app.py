import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

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


class BackgroundOperation(BaseModel):
    operation_id: str
    customer_id: str
    feature: str
    status: str
    failure_code: str
    retry_allowed: bool
    latest_run_status: str
    updated_at: datetime


class RetryOperationRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=200)


class RetryOperationResponse(BaseModel):
    status: Literal["accepted"]
    external_reference: str
    operation: BackgroundOperation


scenario_file = Path(__file__).parent / "scenarios" / "v1.json"
scenario_data = json.loads(scenario_file.read_text(encoding="utf-8"))
customer_accounts: dict[str, CustomerAccount] = {}
customer_product_contexts: dict[str, CustomerProductContext] = {}
customer_recent_activities: dict[str, CustomerRecentActivity] = {}
event_notification_deliveries: list[EventNotificationDelivery] = []
background_operations: dict[str, BackgroundOperation] = {}

for scenario in scenario_data["scenarios"]:
    account = CustomerAccount.model_validate(scenario["account"])
    customer_accounts[account.customer_id] = account
    customer_product_contexts[account.customer_id] = CustomerProductContext.model_validate(scenario["product_context"])
    customer_recent_activities[account.customer_id] = CustomerRecentActivity.model_validate(scenario["recent_activity"])

    for delivery in scenario["deliveries"]:
        event_notification_deliveries.append(EventNotificationDelivery.model_validate(delivery))

    if scenario["background_operation"] is not None:
        background_operations[account.customer_id] = BackgroundOperation.model_validate(scenario["background_operation"])

initial_background_operations = {customer_id: operation.model_copy(deep=True) for customer_id, operation in background_operations.items()}
retry_results: dict[str, RetryOperationResponse] = {}
retry_execution_counts: dict[str, int] = {}

platform_statuses: dict[str, PlatformStatus] = {}

for status_data in scenario_data["platform_statuses"]:
    platform_status = PlatformStatus.model_validate(status_data)
    platform_statuses[platform_status.service] = platform_status


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

    return customer_product_contexts[customer_id]


@app.get("/customers/{customer_id}/recent-activity", response_model=CustomerRecentActivity)
def get_customer_recent_activity(customer_id: str) -> CustomerRecentActivity:
    activity = customer_recent_activities.get(customer_id)

    if activity is None:
        raise HTTPException(status_code=404, detail="Recent customer activity not found")

    return activity


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
def get_platform_status(service: Literal["event_notifications", "report_exports"] = "event_notifications") -> PlatformStatus:
    return platform_statuses[service]


@app.get("/customers/{customer_id}/background-operation", response_model=BackgroundOperation)
def get_background_operation(customer_id: str) -> BackgroundOperation:
    operation = background_operations.get(customer_id)

    if operation is None:
        raise HTTPException(status_code=404, detail="Customer background operation not found")

    return operation


@app.post("/customers/{customer_id}/background-operations/{operation_id}/retry", response_model=RetryOperationResponse)
def retry_background_operation(customer_id: str, operation_id: str, request: RetryOperationRequest) -> RetryOperationResponse:
    saved_result = retry_results.get(request.idempotency_key)

    if saved_result is not None:
        if saved_result.operation.customer_id != customer_id or saved_result.operation.operation_id != operation_id:
            raise HTTPException(status_code=409, detail="Idempotency key belongs to a different operation")
        return saved_result

    operation = background_operations.get(customer_id)

    if operation is None or operation.operation_id != operation_id:
        raise HTTPException(status_code=404, detail="Customer background operation not found")

    if operation.status != "failed" or operation.latest_run_status != "failed" or operation.failure_code != "dependency_timeout" or operation.retry_allowed is False:
        raise HTTPException(status_code=409, detail="Background operation is not eligible for retry")

    operation.status = "succeeded"
    operation.latest_run_status = "succeeded"
    operation.retry_allowed = False
    operation.updated_at = datetime.now(timezone.utc)
    result = RetryOperationResponse(status="accepted", external_reference=f"retry-{operation.operation_id}", operation=operation.model_copy(deep=True))
    retry_results[request.idempotency_key] = result
    retry_execution_counts[request.idempotency_key] = retry_execution_counts.get(request.idempotency_key, 0) + 1
    return result


def reset_action_state() -> None:
    background_operations.clear()
    background_operations.update({customer_id: operation.model_copy(deep=True) for customer_id, operation in initial_background_operations.items()})
    retry_results.clear()
    retry_execution_counts.clear()
