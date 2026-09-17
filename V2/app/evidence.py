import json
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel

from app.db import get_connection

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


def save_evidence(case_id: str, company_id: str, batch_id: str, parallel: bool, model_tool_call_id: str | None, tool_name: str, request: dict[str, object], response: dict[str, object], source_service: str, source_record_id: str | None, status: EvidenceStatus, latency_ms: int, trace_id: str | None) -> EvidenceRecord:
    evidence_id = str(uuid4())
    with get_connection() as connection:
        sequence = connection.execute("SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM support.evidence WHERE case_id = %s", (case_id,)).fetchone()["next_sequence"]
        connection.execute(
            """INSERT INTO support.evidence (evidence_id, case_id, company_id, sequence, batch_id, parallel, model_tool_call_id, tool_name, request, response, source_service, source_record_id, status, latency_ms, trace_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s)""",
            (evidence_id, case_id, company_id, sequence, batch_id, parallel, model_tool_call_id, tool_name, json.dumps(request), json.dumps(response), source_service, source_record_id, status, latency_ms, trace_id),
        )
    return EvidenceRecord(evidence_id=evidence_id, sequence=sequence, batch_id=batch_id, parallel=parallel, model_tool_call_id=model_tool_call_id, tool_name=tool_name, request=request, response=response, source_service=source_service, source_record_id=source_record_id, status=status, latency_ms=latency_ms, trace_id=trace_id)


def load_evidence(case_id: str) -> list[EvidenceRecord]:
    with get_connection() as connection:
        rows = connection.execute("""SELECT evidence_id::text, sequence, batch_id::text, parallel, model_tool_call_id, tool_name, request, response, source_service, source_record_id, status, latency_ms, trace_id
            FROM support.evidence WHERE case_id = %s ORDER BY sequence""", (case_id,)).fetchall()
    return [EvidenceRecord(**row) for row in rows]

