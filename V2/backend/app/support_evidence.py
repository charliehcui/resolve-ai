import json
from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel

from backend.app.database import get_connection

EvidenceStatus = Literal["success", "empty", "not_found", "forbidden", "unavailable", "error"]


class EvidenceRecord(BaseModel):
    evidence_id: str
    sequence: int
    batch_id: str
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


def optional_string(value: object) -> str | None:
    text = str(value or "")
    if not text:
        return None
    return text


def optional_integer(value: object) -> int | None:
    if isinstance(value, int):
        return value
    return None


def evidence_metadata(tool_name: str, request: dict[str, object], response: dict[str, object]) -> tuple[str | None, str | None, int | None]:
    if tool_name == "GetStockFacts":
        object_id = optional_string(request.get("sku"))
        source_version = optional_integer(response.get("source_version"))
        return "stock", object_id, source_version

    if tool_name in {"GetShipment", "GetShipmentRecords", "GetPlatformShipment"}:
        version = response.get("shipment_version") or response.get("version")
        object_id = optional_string(request.get("order_id"))
        source_version = optional_integer(version)
        return "shipment", object_id, source_version

    if tool_name in {"GetOrder", "GetProcessRecords"}:
        object_id = optional_string(request.get("order_id"))
        source_version = optional_integer(response.get("version"))
        return "order", object_id, source_version

    if tool_name in {"GetShopStatus", "CheckConnection"}:
        object_id = optional_string(request.get("shop_id"))
        source_version = optional_integer(response.get("version"))
        return "shop", object_id, source_version

    return None, None, None


def save_evidence(case_id: str, company_id: str, batch_id: str, parallel: bool, model_tool_call_id: str | None, tool_name: str, request: dict[str, object], response: dict[str, object], source_service: str, source_record_id: str | None, status: EvidenceStatus, latency_ms: int, trace_id: str | None) -> EvidenceRecord:
    evidence_id = str(uuid4())
    object_type, object_id, source_version = evidence_metadata(tool_name, request, response)
    with get_connection() as connection:
        sequence = connection.execute("SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM support.evidence WHERE case_id = %s", (case_id,)).fetchone()["next_sequence"]
        connection.execute(
            """INSERT INTO support.evidence (evidence_id, case_id, company_id, sequence, batch_id, parallel, model_tool_call_id, tool_name, request, response, source_service, source_record_id, status, latency_ms, trace_id, object_type, object_id, source_version)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (evidence_id, case_id, company_id, sequence, batch_id, parallel, model_tool_call_id, tool_name, json.dumps(request, default=str), json.dumps(response, default=str), source_service, source_record_id, status, latency_ms, trace_id, object_type, object_id, source_version),
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
        rows = connection.execute("""SELECT evidence_id::text, sequence, batch_id::text, parallel, model_tool_call_id, tool_name, request, response, source_service, source_record_id, status, latency_ms, trace_id, object_type, object_id, observed_at, source_version
            FROM support.evidence WHERE case_id = %s ORDER BY sequence""", (case_id,)).fetchall()
    evidence: list[EvidenceRecord] = []
    for row in rows:
        evidence.append(EvidenceRecord(**row))

    return evidence
