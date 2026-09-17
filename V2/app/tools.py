import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from uuid import uuid4

import httpx
from langsmith import traceable
from pydantic import BaseModel, Field

from app.evidence import EvidenceRecord, EvidenceStatus, save_evidence
from app.models import AuthContext
from app.trace import current_trace_id
from services.common import read_service_token


class GetShopStatus(BaseModel):
    """Read the current order-sync setting for one known shop."""

    shop_id: str = Field(description="The exact known shop ID")


class GetOrder(BaseModel):
    """Read one source platform order in the current company scope."""

    shop_id: str = Field(description="The exact known shop ID")
    order_id: str = Field(description="The exact known external order ID")


class GetProcessRecords(BaseModel):
    """Read merchant receipt, processing task, and merchant-order facts for one known order."""

    shop_id: str = Field(description="The exact known shop ID")
    order_id: str = Field(description="The exact known external order ID")


class CheckConnection(BaseModel):
    """Read the current connection state for one known shop."""

    shop_id: str = Field(description="The exact known shop ID")


READ_TOOL_SCHEMAS = [GetShopStatus, GetOrder, GetProcessRecords, CheckConnection]


class ToolResult(BaseModel):
    tool_name: str
    request: dict[str, object]
    response: dict[str, object]
    source_service: str
    source_record_id: str | None = None
    status: EvidenceStatus
    latency_ms: int
    trace_id: str | None = None


def service_headers(company_id: str) -> dict[str, str]:
    return {"X-Service-Token": read_service_token("support-read"), "X-Company-ID": company_id}


def request_fact(tool_name: str, source_service: str, url: str, company_id: str, request: dict[str, object]) -> ToolResult:
    started = time.perf_counter()
    try:
        response = httpx.get(url, headers=service_headers(company_id), params=request, timeout=5)
        latency_ms = int((time.perf_counter() - started) * 1000)
        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {"http_status": response.status_code}
        if response.status_code == 404:
            status: EvidenceStatus = "not_found"
        elif response.status_code in {401, 403}:
            status = "forbidden"
        elif not response.is_success:
            status = "error"
        elif body.get("empty") is True:
            status = "empty"
        else:
            status = "success"
        source_record_id = str(body.get("event_id") or body.get("task_id") or body.get("shop_id") or "") or None
        return ToolResult(tool_name=tool_name, request=request, response=body, source_service=source_service, source_record_id=source_record_id, status=status, latency_ms=latency_ms, trace_id=current_trace_id())
    except httpx.RequestError as error:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return ToolResult(tool_name=tool_name, request=request, response={"error_type": type(error).__name__}, source_service=source_service, status="unavailable", latency_ms=latency_ms, trace_id=current_trace_id())


@traceable(name="support_get_shop_status", run_type="tool")
def get_shop_status(auth: AuthContext, shop_id: str) -> ToolResult:
    base_url = os.getenv("MERCHANT_URL", "http://127.0.0.1:8002")
    result = request_fact("GetShopStatus", "merchant", f"{base_url}/internal/shops/{shop_id}/status", auth.company_id, {})
    result.request = {"shop_id": shop_id}
    return result


@traceable(name="support_get_order", run_type="tool")
def get_order(auth: AuthContext, shop_id: str, order_id: str) -> ToolResult:
    base_url = os.getenv("PLATFORM_URL", "http://127.0.0.1:8001")
    result = request_fact("GetOrder", "platform", f"{base_url}/internal/orders/{order_id}", auth.company_id, {"shop_id": shop_id})
    result.request = {"shop_id": shop_id, "order_id": order_id}
    return result


@traceable(name="support_get_process_records", run_type="tool")
def get_process_records(auth: AuthContext, shop_id: str, order_id: str) -> ToolResult:
    base_url = os.getenv("MERCHANT_URL", "http://127.0.0.1:8002")
    result = request_fact("GetProcessRecords", "merchant", f"{base_url}/internal/process-records/{order_id}", auth.company_id, {"shop_id": shop_id})
    result.request = {"shop_id": shop_id, "order_id": order_id}
    return result


@traceable(name="support_check_connection", run_type="tool")
def check_connection(auth: AuthContext, shop_id: str) -> ToolResult:
    base_url = os.getenv("MERCHANT_URL", "http://127.0.0.1:8002")
    result = request_fact("CheckConnection", "merchant", f"{base_url}/internal/shops/{shop_id}/connection", auth.company_id, {})
    result.request = {"shop_id": shop_id}
    return result


TOOL_FUNCTIONS: dict[str, Callable[..., ToolResult]] = {
    "GetShopStatus": get_shop_status,
    "GetOrder": get_order,
    "GetProcessRecords": get_process_records,
    "CheckConnection": check_connection,
}


def validate_tool_call(tool_call: dict[str, object], shop_id: str, order_id: str) -> str | None:
    name = str(tool_call.get("name", ""))
    args = tool_call.get("args") or {}
    if name not in TOOL_FUNCTIONS:
        return "TOOL_NOT_ALLOWED"
    if not isinstance(args, dict):
        return "INVALID_TOOL_INPUT"
    if args.get("shop_id") != shop_id:
        return "SHOP_SCOPE_MISMATCH"
    if name in {"GetOrder", "GetProcessRecords"} and args.get("order_id") != order_id:
        return "ORDER_SCOPE_MISMATCH"
    return None


def call_tool(auth: AuthContext, tool_call: dict[str, object]) -> ToolResult:
    name = str(tool_call["name"])
    args = dict(tool_call.get("args") or {})
    return TOOL_FUNCTIONS[name](auth, **args)


@traceable(name="support_parallel_tool_batch", run_type="chain")
def execute_tool_batch(case_id: str, auth: AuthContext, tool_calls: list[dict[str, object]], shop_id: str, order_id: str) -> list[EvidenceRecord]:
    batch_id = str(uuid4())
    valid_calls: list[dict[str, object]] = []
    results: list[tuple[dict[str, object], ToolResult]] = []
    for tool_call in tool_calls:
        validation_error = validate_tool_call(tool_call, shop_id, order_id)
        if validation_error:
            result = ToolResult(tool_name=str(tool_call.get("name", "unknown")), request=dict(tool_call.get("args") or {}), response={"error_code": validation_error}, source_service="support", status="error", latency_ms=0, trace_id=current_trace_id())
            results.append((tool_call, result))
        else:
            valid_calls.append(tool_call)
    parallel = len(valid_calls) > 1
    if parallel:
        with ThreadPoolExecutor(max_workers=len(valid_calls)) as executor:
            futures = [(tool_call, executor.submit(copy_context().run, call_tool, auth, tool_call)) for tool_call in valid_calls]
            results.extend((tool_call, future.result()) for tool_call, future in futures)
    else:
        results.extend((tool_call, call_tool(auth, tool_call)) for tool_call in valid_calls)
    evidence = []
    for tool_call, result in results:
        evidence.append(save_evidence(case_id, auth.company_id, batch_id, parallel, str(tool_call.get("id") or "") or None, result.tool_name, result.request, result.response, result.source_service, result.source_record_id, result.status, result.latency_ms, result.trace_id))
    return evidence
