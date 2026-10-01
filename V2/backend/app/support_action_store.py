import json
from uuid import uuid4

from backend.app.database import get_connection
from backend.app.models import UserContext
from backend.app.support_action_registry import action_policy
from backend.app.support_evidence import EvidenceRecord, save_evidence
from backend.app.support_tools import ReadToolResult
from backend.app.trace import current_trace_id


def save_action_step(action_id: str, step_name: str, status: str, details: dict[str, object], evidence_id: str | None = None) -> None:
    with get_connection() as connection:
        connection.execute("INSERT INTO support.action_steps (step_id, action_id, step_name, status, details, evidence_id, trace_id) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)", (str(uuid4()), action_id, step_name, status, json.dumps(details, default=str), evidence_id, current_trace_id()))


def save_read_tool_evidence(case_id: str, user: UserContext, tool_result: ReadToolResult) -> str:
    evidence_record = save_evidence(case_id, user.company_id, str(uuid4()), False, None, tool_result.tool_name, tool_result.request, tool_result.response, tool_result.source_service, tool_result.source_record_id, tool_result.status, tool_result.latency_ms, tool_result.trace_id)

    return evidence_record.evidence_id


def index_evidence_by_tool(records: list[EvidenceRecord]) -> dict[str, EvidenceRecord]:
    evidence_by_tool_name: dict[str, EvidenceRecord] = {}

    for record in records:
        evidence_by_tool_name[record.tool_name] = record

    return evidence_by_tool_name


def get_evidence_ids(records: list[EvidenceRecord]) -> list[str]:
    evidence_ids: list[str] = []

    for record in records:
        evidence_ids.append(record.evidence_id)

    return evidence_ids


def get_total_evidence_latency_ms(records: list[EvidenceRecord]) -> int:
    total_latency_ms = 0

    for record in records:
        total_latency_ms += record.latency_ms

    return total_latency_ms


def fields_match(actual_values: dict[str, object], expected_values: dict[str, object], field_names: tuple[str, ...]) -> bool:
    for field_name in field_names:
        if actual_values.get(field_name) != expected_values.get(field_name):
            return False

    return True


def get_action_plan(user: UserContext, action_id: str) -> dict[str, object]:
    with get_connection() as connection:
        action_row = connection.execute("""SELECT action_id, case_id, company_id, shop_id, external_order_id, action_type, status, source_event_id, source_version, shop_version, source_snapshot, enable_order_sync, enable_shipment_sync, evidence_ids, idempotency_key, proposed_by, expires_at, created_at, updated_at
            FROM support.action_proposals p WHERE action_id = %s AND company_id = %s
            AND EXISTS (SELECT 1 FROM support.cases c JOIN support.conversations v ON v.conversation_id = c.conversation_id
                WHERE c.case_id = p.case_id AND (v.user_id = %s OR %s = 'admin'))""", (action_id, user.company_id, user.user_id, user.role)).fetchone()

    if action_row is None:
        raise PermissionError("Action is not available in this company scope")

    if user.role == "engineer":
        raise PermissionError("Engineer identities cannot use merchant actions")
    return dict(action_row)


def get_action_details(user: UserContext, action_id: str) -> dict[str, object]:
    action_plan = get_action_plan(user, action_id)

    with get_connection() as connection:
        decision_row = connection.execute("SELECT decision_id::text, decision, decided_by, decided_at FROM support.action_decisions WHERE action_id = %s", (action_id,)).fetchone()
        execution_row = connection.execute("SELECT execution_id::text, request_id::text, status, claim_until, attempts, receipt, error_type, trace_id, created_at, updated_at FROM support.action_executions WHERE action_id = %s", (action_id,)).fetchone()
        verification_row = connection.execute("SELECT verification_id::text, status, details, evidence_ids, trace_id, checked_at FROM support.action_verifications WHERE action_id = %s", (action_id,)).fetchone()
        step_rows = connection.execute("SELECT step_id::text, step_name, status, details, evidence_id::text, trace_id, created_at FROM support.action_steps WHERE action_id = %s ORDER BY created_at", (action_id,)).fetchall()

    action_details = dict(action_plan)
    action_details["action_id"] = str(action_details["action_id"])
    action_details["case_id"] = str(action_details["case_id"])
    if action_details["source_event_id"] is not None:
        action_details["source_event_id"] = str(action_details["source_event_id"])

    if decision_row is None:
        action_details["decision"] = None
    else:
        action_details["decision"] = dict(decision_row)

    if execution_row is None:
        action_details["execution"] = None
    else:
        action_details["execution"] = dict(execution_row)

    if verification_row is None:
        action_details["verification"] = None
    else:
        action_details["verification"] = dict(verification_row)

    action_steps: list[dict[str, object]] = []

    for step_row in step_rows:
        action_steps.append(dict(step_row))

    action_details["steps"] = action_steps
    policy = action_policy(action_details)
    action_details["risk_level"] = policy["risk_level"]
    action_details["approval_requirement"] = policy["approval_requirement"]

    return action_details

