import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from uuid import uuid4

import httpx
from langsmith import traceable
from pydantic import BaseModel, Field

from backend.app.models import AuthContext
from backend.app.stock import assess_stock_facts
from backend.app.support_evidence import EvidenceRecord, EvidenceStatus, save_evidence
from backend.app.trace import current_trace_id
from simulator.services.common import read_service_token


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


class GetShipment(BaseModel):
    """Read the warehouse order and actual shipment fact for one known order."""

    shop_id: str = Field(description="The exact known shop ID")
    order_id: str = Field(description="The exact known external order ID")


class GetShipmentRecords(BaseModel):
    """Read merchant shipment receipt, processing, and forwarding facts."""

    shop_id: str = Field(description="The exact known shop ID")
    order_id: str = Field(description="The exact known external order ID")


class GetPlatformShipment(BaseModel):
    """Read the platform's accepted shipment fact for one known order."""

    shop_id: str = Field(description="The exact known shop ID")
    order_id: str = Field(description="The exact known external order ID")


class GetStockFacts(BaseModel):
    """Read warehouse, merchant rule/task, and platform stock facts without changing stock."""

    shop_id: str = Field(description="The exact known shop ID")
    sku: str = Field(description="The exact known platform SKU")


READ_TOOL_SCHEMAS = [GetShopStatus, GetOrder, GetProcessRecords, CheckConnection, GetShipment, GetShipmentRecords, GetPlatformShipment, GetStockFacts]


class ToolResult(BaseModel):
    tool_name: str
    request: dict[str, object]
    response: dict[str, object]
    source_service: str
    source_record_id: str | None = None
    status: EvidenceStatus
    latency_ms: int
    trace_id: str | None = None


def service_headers(company_id: str, request_id: str | None = None) -> dict[str, str]:
    headers = {"X-Service-Token": read_service_token("support-read"), "X-Company-ID": company_id}
    if request_id:
        headers["X-Request-ID"] = request_id
    return headers


def response_status(response: httpx.Response, body: dict[str, object]) -> EvidenceStatus:
    if response.status_code == 404:
        return "not_found"
    if response.status_code in {401, 403}:
        return "forbidden"
    if not response.is_success:
        return "error"
    if body.get("empty") is True:
        return "empty"
    return "success"


def error_response_body(response: httpx.Response, body: dict[str, object], request_id: str) -> dict[str, object]:
    error_codes = {
        401: "AUTHENTICATION_REQUIRED",
        403: "FORBIDDEN",
        429: "RATE_LIMITED",
        500: "INTERNAL_ERROR",
        503: "SERVICE_UNAVAILABLE",
    }
    error_code = error_codes.get(response.status_code, f"HTTP_{response.status_code}")
    response_request_id = response.headers.get("X-Request-ID", request_id)
    retryable = response.status_code in {429, 500, 503}

    return {
        **body,
        "http_status": response.status_code,
        "error_code": error_code,
        "request_id": response_request_id,
        "retryable": retryable,
    }


def source_record_id_from(body: dict[str, object]) -> str | None:
    source_record_id = body.get("event_id") or body.get("task_id") or body.get("shop_id")
    if source_record_id:
        return str(source_record_id)
    return None


def request_fact(tool_name: str, source_service: str, url: str, company_id: str, request: dict[str, object]) -> ToolResult:
    started = time.perf_counter()
    request_id = str(uuid4())

    try:
        headers = service_headers(company_id, request_id)
        response = httpx.get(url, headers=headers, params=request, timeout=5)
        latency_ms = int((time.perf_counter() - started) * 1000)

        content_type = response.headers.get("content-type", "")
        if content_type.startswith("application/json"):
            body = response.json()
        else:
            body = {"http_status": response.status_code}

        status = response_status(response, body)
        if not response.is_success:
            body = error_response_body(response, body, request_id)

        source_record_id = source_record_id_from(body)
        return ToolResult(
            tool_name=tool_name,
            request=request,
            response=body,
            source_service=source_service,
            source_record_id=source_record_id,
            status=status,
            latency_ms=latency_ms,
            trace_id=current_trace_id(),
        )
    except httpx.RequestError as error:
        latency_ms = int((time.perf_counter() - started) * 1000)
        if isinstance(error, httpx.TimeoutException):
            error_code = "TIMEOUT"
        else:
            error_code = "SERVICE_UNAVAILABLE"

        error_body = {
            "error_type": type(error).__name__,
            "error_code": error_code,
            "request_id": request_id,
            "retryable": True,
        }
        return ToolResult(
            tool_name=tool_name,
            request=request,
            response=error_body,
            source_service=source_service,
            status="unavailable",
            latency_ms=latency_ms,
            trace_id=current_trace_id(),
        )


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


@traceable(name="support_get_shipment", run_type="tool")
def get_shipment(auth: AuthContext, shop_id: str, order_id: str) -> ToolResult:
    base_url = os.getenv("WAREHOUSE_URL", "http://127.0.0.1:8003")
    result = request_fact("GetShipment", "warehouse", f"{base_url}/internal/shipments/{order_id}", auth.company_id, {"shop_id": shop_id})
    result.request = {"shop_id": shop_id, "order_id": order_id}
    return result


@traceable(name="support_get_shipment_records", run_type="tool")
def get_shipment_records(auth: AuthContext, shop_id: str, order_id: str) -> ToolResult:
    base_url = os.getenv("MERCHANT_URL", "http://127.0.0.1:8002")
    result = request_fact("GetShipmentRecords", "merchant", f"{base_url}/internal/shipment-records/{order_id}", auth.company_id, {"shop_id": shop_id})
    result.request = {"shop_id": shop_id, "order_id": order_id}
    return result


@traceable(name="support_get_platform_shipment", run_type="tool")
def get_platform_shipment(auth: AuthContext, shop_id: str, order_id: str) -> ToolResult:
    base_url = os.getenv("PLATFORM_URL", "http://127.0.0.1:8001")
    result = request_fact("GetPlatformShipment", "platform", f"{base_url}/internal/shipments/{order_id}", auth.company_id, {"shop_id": shop_id})
    result.request = {"shop_id": shop_id, "order_id": order_id}
    return result


@traceable(name="support_get_stock_facts", run_type="tool")
def get_stock_facts(auth: AuthContext, shop_id: str, sku: str) -> ToolResult:
    merchant_url = os.getenv("MERCHANT_URL", "http://127.0.0.1:8002")
    merchant = request_fact("GetStockFacts", "merchant", f"{merchant_url}/internal/stock-records/{sku}", auth.company_id, {"shop_id": shop_id})
    request = {"shop_id": shop_id, "sku": sku}

    if merchant.status == "empty":
        assessment = assess_stock_facts(merchant.response, None, None)
        response = {"merchant": merchant.response, **assessment}
        return ToolResult(
            tool_name="GetStockFacts",
            request=request,
            response=response,
            source_service="merchant",
            status="empty",
            latency_ms=merchant.latency_ms,
            trace_id=current_trace_id(),
        )

    if merchant.status != "success":
        merchant.request = request
        return merchant

    rule = merchant.response.get("rule")
    if not isinstance(rule, dict) or not rule.get("warehouse_sku"):
        response = {
            "merchant": merchant.response,
            "assessment": "insufficient_information",
            "reason": "STOCK_MAPPING_MISSING",
        }
        return ToolResult(
            tool_name="GetStockFacts",
            request=request,
            response=response,
            source_service="merchant",
            status="empty",
            latency_ms=merchant.latency_ms,
            trace_id=current_trace_id(),
        )

    warehouse_url = os.getenv("WAREHOUSE_URL", "http://127.0.0.1:8003")
    platform_url = os.getenv("PLATFORM_URL", "http://127.0.0.1:8001")
    with ThreadPoolExecutor(max_workers=2) as executor:
        warehouse_stock_url = f"{warehouse_url}/internal/stocks/{rule['warehouse_sku']}"
        platform_stock_url = f"{platform_url}/internal/stocks/{sku}"
        warehouse_future = executor.submit(copy_context().run, request_fact, "GetStockFacts", "warehouse", warehouse_stock_url, auth.company_id, {})
        platform_future = executor.submit(copy_context().run, request_fact, "GetStockFacts", "platform", platform_stock_url, auth.company_id, {"shop_id": shop_id})
        warehouse = warehouse_future.result()
        platform = platform_future.result()

    warehouse_body = warehouse.response if warehouse.status == "success" else None
    platform_body = platform.response if platform.status == "success" else None
    assessment = assess_stock_facts(merchant.response, warehouse_body, platform_body)
    status: EvidenceStatus = "success"
    if warehouse.status in {"forbidden", "unavailable", "error"}:
        status = warehouse.status
    elif platform.status in {"forbidden", "unavailable", "error"}:
        status = platform.status

    response = {"merchant": merchant.response, "warehouse": warehouse_body or warehouse.response, "platform": platform_body, **assessment}
    total_latency_ms = merchant.latency_ms + warehouse.latency_ms + platform.latency_ms
    return ToolResult(
        tool_name="GetStockFacts",
        request=request,
        response=response,
        source_service="merchant+warehouse+platform",
        source_record_id=sku,
        status=status,
        latency_ms=total_latency_ms,
        trace_id=current_trace_id(),
    )


TOOL_FUNCTIONS: dict[str, Callable[..., ToolResult]] = {
    "GetShopStatus": get_shop_status,
    "GetOrder": get_order,
    "GetProcessRecords": get_process_records,
    "CheckConnection": check_connection,
    "GetShipment": get_shipment,
    "GetShipmentRecords": get_shipment_records,
    "GetPlatformShipment": get_platform_shipment,
    "GetStockFacts": get_stock_facts,
}


def validate_tool_call(tool_call: dict[str, object], shop_id: str, order_id: str, sku: str = "") -> str | None:
    name = str(tool_call.get("name", ""))
    args = tool_call.get("args") or {}
    if name not in TOOL_FUNCTIONS:
        return "TOOL_NOT_ALLOWED"
    if not isinstance(args, dict):
        return "INVALID_TOOL_INPUT"
    if args.get("shop_id") != shop_id:
        return "SHOP_SCOPE_MISMATCH"
    if name in {"GetOrder", "GetProcessRecords", "GetShipment", "GetShipmentRecords", "GetPlatformShipment"} and args.get("order_id") != order_id:
        return "ORDER_SCOPE_MISMATCH"
    if name == "GetStockFacts" and args.get("sku") != sku:
        return "SKU_SCOPE_MISMATCH"
    return None


def call_tool(auth: AuthContext, tool_call: dict[str, object]) -> ToolResult:
    name = str(tool_call["name"])
    args = dict(tool_call.get("args") or {})
    return TOOL_FUNCTIONS[name](auth, **args)


@traceable(name="support_parallel_tool_batch", run_type="chain")
def execute_tool_batch(case_id: str, auth: AuthContext, tool_calls: list[dict[str, object]], shop_id: str, order_id: str, sku: str = "") -> list[EvidenceRecord]:
    batch_id = str(uuid4())
    valid_calls: list[dict[str, object]] = []
    results: list[tuple[dict[str, object], ToolResult]] = []
    for tool_call in tool_calls:
        validation_error = validate_tool_call(tool_call, shop_id, order_id, sku)
        if validation_error:
            result = ToolResult(
                tool_name=str(tool_call.get("name", "unknown")),
                request=dict(tool_call.get("args") or {}),
                response={"error_code": validation_error},
                source_service="support",
                status="error",
                latency_ms=0,
                trace_id=current_trace_id(),
            )
            results.append((tool_call, result))
        else:
            valid_calls.append(tool_call)

    parallel = len(valid_calls) > 1
    if parallel:
        with ThreadPoolExecutor(max_workers=len(valid_calls)) as executor:
            futures = []
            for tool_call in valid_calls:
                future = executor.submit(copy_context().run, call_tool, auth, tool_call)
                futures.append((tool_call, future))

            for tool_call, future in futures:
                results.append((tool_call, future.result()))
    else:
        for tool_call in valid_calls:
            results.append((tool_call, call_tool(auth, tool_call)))

    evidence: list[EvidenceRecord] = []
    for tool_call, result in results:
        model_tool_call_id = str(tool_call.get("id") or "") or None
        record = save_evidence(case_id, auth.company_id, batch_id, parallel, model_tool_call_id, result.tool_name, result.request, result.response, result.source_service, result.source_record_id, result.status, result.latency_ms, result.trace_id)
        evidence.append(record)

    return evidence


@traceable(name="create_engineer_ticket", run_type="tool")
def create_ticket(auth: AuthContext, conversation_id: str, trigger: str, reason: str) -> dict[str, object]:
    """Create an Engineer Ticket through deterministic policy checks.

    This controlled write is intentionally not registered in READ_TOOL_SCHEMAS or
    TOOL_FUNCTIONS, so the Support Agent cannot invoke it as a model-selected tool.
    """
    from backend.app.tickets import create_ticket as create_engineer_ticket

    return create_engineer_ticket(auth, conversation_id, trigger, reason)  # type: ignore[arg-type]
