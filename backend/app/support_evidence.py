import json
from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel

from backend.app.database import get_connection

#把 Tool 查到的结果正式保存成 Evidence，并且以后可以重新读取给 Support Agent 使用
EvidenceStatus = Literal["success", "empty", "not_found", "forbidden", "unavailable", "error"]


class EvidenceRecord(BaseModel):
    evidence_id: str   # 这条 Evidence 自己的唯一编号
    sequence: int     #这个 Case 的第几条 Evidence
    batch_id: str   #这条 Evidence 属于哪一批查询
    parallel: bool
    model_tool_call_id: str | None = None
    tool_name: str
    request: dict[str, object]
    response: dict[str, object]
    source_service: str
    source_record_id: str | None = None
    status: EvidenceStatus
    latency_ms: int
    trace_id: str | None = None
    object_type: str | None = None
    object_id: str | None = None
    observed_at: datetime | None = None
    source_version: int | None = None


def is_read_validation_error(record: EvidenceRecord) -> bool:
    return record.source_service == "support" and record.status == "error"


def to_optional_string(value: object) -> str | None:
    if value is None:
        return None

    text = str(value)

    if text == "":
        return None

    return text


def to_optional_integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        return value

    return None

#根据 Tool 类型，自动判断这条 Evidence 描述的是什么
def extract_evidence_metadata(
    tool_name: str,
    request: dict[str, object],
    response: dict[str, object],
) -> tuple[str | None, str | None, int | None]:

    if tool_name == "GetWorkerTask":
        return "task", to_optional_string(response.get("task_id")), to_optional_integer(response.get("version"))
    if tool_name == "GetStockStatus":
        object_id = to_optional_string(request.get("sku"))
        source_version = to_optional_integer(response.get("source_version"))

        return "stock", object_id, source_version

    if tool_name in {"GetWarehouseShipment", "GetShipmentProcessRecords", "GetPlatformShipment"}:
        version = response.get("shipment_version")

        if version is None:
            version = response.get("version")

        object_id = to_optional_string(request.get("order_id"))
        source_version = to_optional_integer(version)

        return "shipment", object_id, source_version

    if tool_name in {"GetOrder", "GetOrderProcessRecords"}:
        object_id = to_optional_string(request.get("order_id"))
        source_version = to_optional_integer(response.get("version"))

        return "order", object_id, source_version

    if tool_name in {"GetShopSyncStatus", "GetShopConnectionStatus"}:
        object_id = to_optional_string(request.get("shop_id"))
        source_version = to_optional_integer(response.get("version"))

        return "shop", object_id, source_version

    return None, None, None

#把 Tool Result 正式变成 Evidence 并保存到数据库
def save_evidence(
    case_id: str,
    company_id: str,
    batch_id: str,
    parallel: bool,
    model_tool_call_id: str | None,
    tool_name: str,
    request: dict[str, object],
    response: dict[str, object],
    source_service: str,
    source_record_id: str | None,
    status: EvidenceStatus,
    latency_ms: int,
    trace_id: str | None,
) -> EvidenceRecord:

    evidence_id = str(uuid4())

    object_type, object_id, source_version = extract_evidence_metadata(
        tool_name,
        request,
        response,
    )

    with get_connection() as connection:
        sequence_row = connection.execute(
            """
            SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
            FROM support.evidence
            WHERE case_id = %s
            """,
            (case_id,),
        ).fetchone()

        sequence = sequence_row["next_sequence"]

        connection.execute(
            """
            INSERT INTO support.evidence (
                evidence_id,
                case_id,
                company_id,
                sequence,
                batch_id,
                parallel,
                model_tool_call_id,
                tool_name,
                request,
                response,
                source_service,
                source_record_id,
                status,
                latency_ms,
                trace_id,
                object_type,
                object_id,
                source_version
            )
            VALUES (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s::jsonb,
                %s::jsonb,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s
            )
            """,
            (
                evidence_id,
                case_id,
                company_id,
                sequence,
                batch_id,
                parallel,
                model_tool_call_id,
                tool_name,
                json.dumps(request, default=str),
                json.dumps(response, default=str),
                source_service,
                source_record_id,
                status,
                latency_ms,
                trace_id,
                object_type,
                object_id,
                source_version,
            ),
        )

    return EvidenceRecord(
        evidence_id=evidence_id,
        sequence=sequence,
        batch_id=batch_id,
        parallel=parallel,
        model_tool_call_id=model_tool_call_id,
        tool_name=tool_name,
        request=request,
        response=response,
        source_service=source_service,
        source_record_id=source_record_id,
        status=status,
        latency_ms=latency_ms,
        trace_id=trace_id,
        object_type=object_type,
        object_id=object_id,
        source_version=source_version,
    )


def load_evidence(case_id: str) -> list[EvidenceRecord]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                evidence_id::text,
                sequence,
                batch_id::text,
                parallel,
                model_tool_call_id,
                tool_name,
                request,
                response,
                source_service,
                source_record_id,
                status,
                latency_ms,
                trace_id,
                object_type,
                object_id,
                observed_at,
                source_version
            FROM support.evidence
            WHERE case_id = %s
            ORDER BY sequence
            """,
            (case_id,),
        ).fetchall()

    evidence: list[EvidenceRecord] = []

    for row in rows:
        evidence.append(EvidenceRecord.model_validate(row))

    return evidence




# support_evidence.py
# 【负责 Evidence 的整理、保存和读取】
#
#
# ReadToolResult
# 【刚刚执行完一次 Read Tool 得到的临时查询结果】
# ↓
#
# save_evidence()
# 【把临时结果正式变成 Evidence】
# ↓
#
# 生成 evidence_id
# 【给这条 Evidence 一个唯一编号】
# ↓
#
# extract_evidence_metadata()
# 【根据 Tool 类型判断这条 Evidence 描述的是什么对象】
#
# 例如：
# GetOrder → order
# GetWarehouseShipment → shipment
# GetStockStatus → stock
# GetWorkerTask → task
# ↓
#
# 得到：
# object_type / object_id / source_version
# 【是什么对象 / 哪个对象 / 当时是什么版本】
# ↓
#
# 生成 sequence
# 【这是当前 Case 的第几条 Evidence】
# ↓
#
# 保存到 PostgreSQL
# ↓
#
# EvidenceRecord
# 【Data Model：正式保存的调查证据】
#
#
# 之后需要重新读取时：
#
# load_evidence(case_id)
# 【读取这个 Case 已经保存的 Evidence】
# ↓
#
# EvidenceRecord[]
# 【按 sequence 顺序返回】
