import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from uuid import uuid4

import httpx
from langsmith import traceable
from pydantic import BaseModel, Field

from backend.app.models import UserContext
from backend.app.stock import assess_stock_facts
from backend.app.support_evidence import EvidenceRecord, EvidenceStatus, save_evidence
from backend.app.trace import current_trace_id
from simulator.services.common import read_service_token


#Support Agent 决定“我要查什么”以后，这个文件负责真正去模拟的业务系统查数据，并把结果保存成 Evidence。
class GetShopSyncStatus(BaseModel):  # 查询店铺当前的订单同步状态
    """What this tool reads: the current order-sync setting for one known shop. When to use this tool: when investigating whether order synchronization is enabled for that shop. What this tool cannot read or determine: it cannot read orders, connection authorization, shipments, or stock, and cannot determine the root cause by itself. What is returned on success: the shop's current order-sync status from the merchant service."""

    shop_id: str = Field(description="The exact known shop ID")


class GetOrder(BaseModel):  # 查询平台中的一个真实订单
    """What this tool reads: one platform order for a known shop and external order ID. When to use this tool: when checking whether the platform has the source order and what facts it contains. What this tool cannot read or determine: it cannot read merchant processing, shop settings, shipments, or stock, and cannot determine why downstream processing failed. What is returned on success: the matching platform order record."""

    shop_id: str = Field(description="The exact known shop ID")
    order_id: str = Field(description="The exact known external order ID")


class GetOrderProcessRecords(BaseModel):  # 查询一个订单在商家系统里的处理记录
    """What this tool reads: merchant receipt, processing task, and merchant-order records for one known order. When to use this tool: when checking how the merchant system processed an order after the platform received it. What this tool cannot read or determine: it cannot read the source platform order, shop settings, shipment processing, or stock, and cannot prove the platform order exists. What is returned on success: the merchant order-processing records or an explicit empty result."""

    shop_id: str = Field(description="The exact known shop ID")
    order_id: str = Field(description="The exact known external order ID")


class GetShopConnectionStatus(BaseModel):  # 查询店铺当前的连接状态
    """What this tool reads: the current connection authorization state for one known shop. When to use this tool: when investigating whether the shop connection can currently access its channel. What this tool cannot read or determine: it cannot read credentials, repair the connection, read orders, shipments, or stock, or determine the complete root cause. What is returned on success: the shop's current connection status from the merchant service."""

    shop_id: str = Field(description="The exact known shop ID")


class GetWarehouseShipment(BaseModel):  # 查询仓库实际发货情况
    """What this tool reads: the warehouse order and actual shipment record for one known order. When to use this tool: when checking whether the warehouse created and dispatched a shipment. What this tool cannot read or determine: it cannot read merchant forwarding records or the platform's accepted shipment, and cannot prove downstream delivery succeeded. What is returned on success: the matching warehouse shipment facts."""

    shop_id: str = Field(description="The exact known shop ID")
    order_id: str = Field(description="The exact known external order ID")


class GetShipmentProcessRecords(BaseModel):  # 查询发货信息在商家系统中的处理和转发记录
    """What this tool reads: merchant shipment receipt, processing, and forwarding records for one known order. When to use this tool: when checking how the merchant system handled warehouse shipment data. What this tool cannot read or determine: it cannot read the warehouse's actual shipment or the platform's accepted shipment, and cannot prove either system has matching data. What is returned on success: the merchant shipment-processing records or an explicit empty result."""

    shop_id: str = Field(description="The exact known shop ID")
    order_id: str = Field(description="The exact known external order ID")


class GetPlatformShipment(BaseModel):  # 查询平台最终收到的发货信息
    """What this tool reads: the platform's accepted shipment record for one known order. When to use this tool: when checking whether shipment data reached the platform. What this tool cannot read or determine: it cannot read the warehouse shipment or merchant forwarding records, and cannot determine where missing data was lost. What is returned on success: the matching platform shipment record."""

    shop_id: str = Field(description="The exact known shop ID")
    order_id: str = Field(description="The exact known external order ID")


class GetStockStatus(BaseModel):  # 查询并综合判断商品库存状态
    """What this tool reads: merchant stock mapping and publish facts plus warehouse and platform stock snapshots for one known SKU. When to use this tool: when comparing stock quantities, versions, and propagation state across those systems. What this tool cannot read or determine: it cannot modify stock, read order or shipment data, or guarantee that a later propagation will complete. What is returned on success: consolidated stock facts and their consistency assessment."""

    shop_id: str = Field(description="The exact known shop ID")
    sku: str = Field(description="The exact known platform SKU")


READ_TOOL_SCHEMAS = [GetShopSyncStatus, GetOrder, GetOrderProcessRecords, GetShopConnectionStatus, GetWarehouseShipment, GetShipmentProcessRecords, GetPlatformShipment, GetStockStatus]


class ReadToolResult(BaseModel):  # 一个只读查询工具执行后的结果
    tool_name: str
    request: dict[str, object]
    response: dict[str, object]
    source_service: str
    source_record_id: str | None = None
    status: EvidenceStatus
    latency_ms: int
    trace_id: str | None = None


def build_service_headers(company_id: str, request_id: str | None = None) -> dict[str, str]:
    headers = {
        "X-Service-Token": read_service_token("support-read"),
        "X-Company-ID": company_id,
    }

    if request_id is not None and request_id != "":
        headers["X-Request-ID"] = request_id

    return headers


def get_evidence_status(response: httpx.Response, body: dict[str, object]) -> EvidenceStatus:
    if response.status_code == 404:
        return "not_found"

    if response.status_code in {401, 403}:
        return "forbidden"

    if response.is_success is False:
        return "error"

    if body.get("empty") is True:
        return "empty"

    return "success"


def build_error_response(response: httpx.Response, body: dict[str, object], request_id: str) -> dict[str, object]:
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

    error_response = dict(body)
    error_response["http_status"] = response.status_code
    error_response["error_code"] = error_code
    error_response["request_id"] = response_request_id
    error_response["retryable"] = retryable

    return error_response


def get_source_record_id(body: dict[str, object]) -> str | None:
    source_record_id = body.get("event_id") or body.get("task_id") or body.get("shop_id")

    if source_record_id is not None and source_record_id != "":
        return str(source_record_id)

    return None


def elapsed_milliseconds(started_at: float) -> int:
    elapsed_seconds = time.perf_counter() - started_at
    return int(elapsed_seconds / 0.001)


def call_read_service(tool_name: str, source_service: str, url: str, company_id: str, request: dict[str, object]) -> ReadToolResult:
    started_at = time.perf_counter()
    request_id = str(uuid4())

    try:
        headers = build_service_headers(company_id, request_id)
        response = httpx.get(url, headers=headers, params=request, timeout=5)
        latency_ms = elapsed_milliseconds(started_at)

        content_type = response.headers.get("content-type", "")

        if content_type.startswith("application/json"):
            body = response.json()
        else:
            body = {
                "http_status": response.status_code,
            }

        status = get_evidence_status(response, body)

        if response.is_success is False:
            body = build_error_response(response, body, request_id)

        source_record_id = get_source_record_id(body)

        return ReadToolResult(
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
        latency_ms = elapsed_milliseconds(started_at)

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

        return ReadToolResult(
            tool_name=tool_name,
            request=request,
            response=error_body,
            source_service=source_service,
            status="unavailable",
            latency_ms=latency_ms,
            trace_id=current_trace_id(),
        )


@traceable(name="support_get_shop_sync_status", run_type="tool")
def get_shop_sync_status(user: UserContext, shop_id: str) -> ReadToolResult:
    base_url = os.getenv("MERCHANT_URL", "http://127.0.0.1:8002")
    result = call_read_service("GetShopSyncStatus", "merchant", f"{base_url}/internal/shops/{shop_id}/status", user.company_id, {})
    result.request = {"shop_id": shop_id}

    return result


@traceable(name="support_get_order", run_type="tool")
def get_order(user: UserContext, shop_id: str, order_id: str) -> ReadToolResult:
    base_url = os.getenv("PLATFORM_URL", "http://127.0.0.1:8001")
    result = call_read_service("GetOrder", "platform", f"{base_url}/internal/orders/{order_id}", user.company_id, {"shop_id": shop_id})
    result.request = {"shop_id": shop_id, "order_id": order_id}

    return result


@traceable(name="support_get_order_process_records", run_type="tool")
def get_order_process_records(user: UserContext, shop_id: str, order_id: str) -> ReadToolResult:
    base_url = os.getenv("MERCHANT_URL", "http://127.0.0.1:8002")
    result = call_read_service("GetOrderProcessRecords", "merchant", f"{base_url}/internal/process-records/{order_id}", user.company_id, {"shop_id": shop_id})
    result.request = {"shop_id": shop_id, "order_id": order_id}

    return result


@traceable(name="support_get_shop_connection_status", run_type="tool")
def get_shop_connection_status(user: UserContext, shop_id: str) -> ReadToolResult:
    base_url = os.getenv("MERCHANT_URL", "http://127.0.0.1:8002")
    result = call_read_service("GetShopConnectionStatus", "merchant", f"{base_url}/internal/shops/{shop_id}/connection", user.company_id, {})
    result.request = {"shop_id": shop_id}

    return result


@traceable(name="support_get_warehouse_shipment", run_type="tool")
def get_warehouse_shipment(user: UserContext, shop_id: str, order_id: str) -> ReadToolResult:
    base_url = os.getenv("WAREHOUSE_URL", "http://127.0.0.1:8003")
    result = call_read_service("GetWarehouseShipment", "warehouse", f"{base_url}/internal/shipments/{order_id}", user.company_id, {"shop_id": shop_id})
    result.request = {"shop_id": shop_id, "order_id": order_id}

    return result


@traceable(name="support_get_shipment_process_records", run_type="tool")
def get_shipment_process_records(user: UserContext, shop_id: str, order_id: str) -> ReadToolResult:
    base_url = os.getenv("MERCHANT_URL", "http://127.0.0.1:8002")
    result = call_read_service("GetShipmentProcessRecords", "merchant", f"{base_url}/internal/shipment-records/{order_id}", user.company_id, {"shop_id": shop_id})
    result.request = {"shop_id": shop_id, "order_id": order_id}

    return result


@traceable(name="support_get_platform_shipment", run_type="tool")
def get_platform_shipment(user: UserContext, shop_id: str, order_id: str) -> ReadToolResult:
    base_url = os.getenv("PLATFORM_URL", "http://127.0.0.1:8001")
    result = call_read_service("GetPlatformShipment", "platform", f"{base_url}/internal/shipments/{order_id}", user.company_id, {"shop_id": shop_id})
    result.request = {"shop_id": shop_id, "order_id": order_id}

    return result


@traceable(name="support_get_stock_status", run_type="tool")
def get_stock_status(user: UserContext, shop_id: str, sku: str) -> ReadToolResult:
    merchant_url = os.getenv("MERCHANT_URL", "http://127.0.0.1:8002")
    merchant = call_read_service("GetStockStatus", "merchant", f"{merchant_url}/internal/stock-records/{sku}", user.company_id, {"shop_id": shop_id})

    request = {
        "shop_id": shop_id,
        "sku": sku,
    }

    if merchant.status == "empty":
        assessment = assess_stock_facts(merchant.response, None, None)

        response = {
            "merchant": merchant.response,
        }

        response.update(assessment)

        return ReadToolResult(
            tool_name="GetStockStatus",
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

    if isinstance(rule, dict) is False:
        warehouse_sku = None
    else:
        warehouse_sku = rule.get("warehouse_sku")

    if warehouse_sku is None or warehouse_sku == "":
        response = {
            "merchant": merchant.response,
            "assessment": "insufficient_information",
            "reason": "STOCK_MAPPING_MISSING",
        }

        return ReadToolResult(
            tool_name="GetStockStatus",
            request=request,
            response=response,
            source_service="merchant",
            status="empty",
            latency_ms=merchant.latency_ms,
            trace_id=current_trace_id(),
        )

    warehouse_url = os.getenv("WAREHOUSE_URL", "http://127.0.0.1:8003")
    platform_url = os.getenv("PLATFORM_URL", "http://127.0.0.1:8001")

    warehouse_stock_url = f"{warehouse_url}/internal/stocks/{warehouse_sku}"
    platform_stock_url = f"{platform_url}/internal/stocks/{sku}"

    with ThreadPoolExecutor(max_workers=2) as executor:
        warehouse_future = executor.submit(
            copy_context().run,
            call_read_service,
            "GetStockStatus",
            "warehouse",
            warehouse_stock_url,
            user.company_id,
            {},
        )

        platform_future = executor.submit(
            copy_context().run,
            call_read_service,
            "GetStockStatus",
            "platform",
            platform_stock_url,
            user.company_id,
            {"shop_id": shop_id},
        )

        warehouse = warehouse_future.result()
        platform = platform_future.result()

    if warehouse.status == "success":
        warehouse_body = warehouse.response
    else:
        warehouse_body = None

    if platform.status == "success":
        platform_body = platform.response
    else:
        platform_body = None

    assessment = assess_stock_facts(merchant.response, warehouse_body, platform_body)

    status: EvidenceStatus = "success"

    if warehouse.status in {"forbidden", "unavailable", "error"}:
        status = warehouse.status
    elif platform.status in {"forbidden", "unavailable", "error"}:
        status = platform.status

    if warehouse_body is not None:
        warehouse_response = warehouse_body
    else:
        warehouse_response = warehouse.response

    response = {
        "merchant": merchant.response,
        "warehouse": warehouse_response,
        "platform": platform_body,
    }

    response.update(assessment)

    total_latency_ms = merchant.latency_ms + warehouse.latency_ms + platform.latency_ms

    return ReadToolResult(
        tool_name="GetStockStatus",
        request=request,
        response=response,
        source_service="merchant+warehouse+platform",
        source_record_id=sku,
        status=status,
        latency_ms=total_latency_ms,
        trace_id=current_trace_id(),
    )


READ_TOOL_FUNCTIONS: dict[str, Callable[..., ReadToolResult]] = {
    "GetShopSyncStatus": get_shop_sync_status,
    "GetOrder": get_order,
    "GetOrderProcessRecords": get_order_process_records,
    "GetShopConnectionStatus": get_shop_connection_status,
    "GetWarehouseShipment": get_warehouse_shipment,
    "GetShipmentProcessRecords": get_shipment_process_records,
    "GetPlatformShipment": get_platform_shipment,
    "GetStockStatus": get_stock_status,
}


def validate_read_tool_call(tool_call: dict[str, object], shop_id: str, order_id: str, sku: str = "") -> str | None:
    name = str(tool_call.get("name", ""))
    args = tool_call.get("args")

    if name in READ_TOOL_FUNCTIONS:
        pass
    else:
        return "TOOL_NOT_ALLOWED"

    if isinstance(args, dict) is False:
        return "INVALID_TOOL_INPUT"

    if args.get("shop_id") != shop_id:
        return "SHOP_SCOPE_MISMATCH"

    order_tools = {
        "GetOrder",
        "GetOrderProcessRecords",
        "GetWarehouseShipment",
        "GetShipmentProcessRecords",
        "GetPlatformShipment",
    }

    if name in order_tools and args.get("order_id") != order_id:
        return "ORDER_SCOPE_MISMATCH"

    if name == "GetStockStatus" and args.get("sku") != sku:
        return "SKU_SCOPE_MISMATCH"

    return None


def execute_read_tool(user: UserContext, tool_call: dict[str, object]) -> ReadToolResult:
    name = str(tool_call["name"])
    args_value = tool_call.get("args")

    if isinstance(args_value, dict) is False:
        raise ValueError("Tool arguments must be a dictionary")

    read_tool = READ_TOOL_FUNCTIONS.get(name)

    if read_tool is None:
        raise ValueError(f"Unsupported read tool: {name}")

    return read_tool(user, **args_value)


@traceable(name="support_parallel_read_tool_batch", run_type="chain")
def execute_read_tool_batch(case_id: str, user: UserContext, tool_calls: list[dict[str, object]], shop_id: str, order_id: str, sku: str = "") -> list[EvidenceRecord]:
    batch_id = str(uuid4())

    valid_calls: list[dict[str, object]] = []
    results: list[tuple[dict[str, object], ReadToolResult]] = []

    for tool_call in tool_calls:
        validation_error = validate_read_tool_call(tool_call, shop_id, order_id, sku)

        if validation_error is not None:
            args_value = tool_call.get("args")

            if isinstance(args_value, dict):
                request = dict(args_value)
            else:
                request = {}

            result = ReadToolResult(
                tool_name=str(tool_call.get("name", "unknown")),
                request=request,
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

    if parallel is True:
        with ThreadPoolExecutor(max_workers=len(valid_calls)) as executor:
            futures = []

            for tool_call in valid_calls:
                future = executor.submit(copy_context().run, execute_read_tool, user, tool_call)
                futures.append((tool_call, future))

            for tool_call, future in futures:
                result = future.result()
                results.append((tool_call, result))

    else:
        for tool_call in valid_calls:
            result = execute_read_tool(user, tool_call)
            results.append((tool_call, result))

    evidence: list[EvidenceRecord] = []

    for tool_call, result in results:
        model_tool_call_id_text = str(tool_call.get("id") or "")

        if model_tool_call_id_text == "":
            model_tool_call_id = None
        else:
            model_tool_call_id = model_tool_call_id_text

        record = save_evidence(
            case_id,
            user.company_id,
            batch_id,
            parallel,
            model_tool_call_id,
            result.tool_name,
            result.request,
            result.response,
            result.source_service,
            result.source_record_id,
            result.status,
            result.latency_ms,
            result.trace_id,
        )

        evidence.append(record)

    return evidence


@traceable(name="create_engineer_ticket", run_type="tool")
def create_engineer_ticket(user: UserContext, conversation_id: str, trigger: str, reason: str) -> dict[str, object]:
    from backend.app.tickets import create_ticket as create_ticket_record

    return create_ticket_record(user, conversation_id, trigger, reason)



# LLM
# ↓
# 提出 Tool Call
# ↓
# validate_read_tool_call()
# 检查查询是否合法
# ↓
# execute_read_tool_batch()
# 真正执行
# ↓
# Platform / Merchant / Warehouse
# ↓
# ReadToolResult     一次查询的临时结果   Support Tool 整理成 ReadToolResult
# ↓
# save_evidence()
# ↓
# EvidenceRecord    给这个查询结果加上身份证，并正式保存
# ↓
# 返回 Support Workflow
# ↓
# LLM 再决定下一步



# Agent 决定查什么
#         ↓
#      Tool Call
#         ↓
# 检查是否允许查询
#         ↓
# 真实/模拟后台系统
#         ↓
#    原始 HTTP Response
#         ↓
#    ReadToolResult
# 统一整理这次查询结果
#         ↓
#    save_evidence()
# 正式登记并保存
#         ↓
#    EvidenceRecord
# 有 Evidence ID 的持久记录
#         ↓
# Support Workflow
#         ↓
# 下一轮 Agent 看到全部 Evidence
#         ↓
# 决定下一步


# validate_read_tool_call()
# ↓
# 先检查：这个查询允不允许执行

# execute_read_tool()   把 LLM 选择的 Tool 名字，转换成真正的 Python 函数调用
# ↓
# 再决定：具体该调用哪个查询函数

# call_read_service()  真正发送请求去后台系统查数据
# ↓
# 最后真的去后台系统拿数据

#为什么需要， 如果没有execute_read_tool， 他就会直接让llm生成工具名称和参数直接执行，这样就没有办法在中间检查工具名称和参数是否合法
#因此我需要一个中间层，execute_read_tool，来把llm生成的工具名称和参数转换成真正的Python函数调用，这样我就可以在中间检查工具名称和参数是否合法
