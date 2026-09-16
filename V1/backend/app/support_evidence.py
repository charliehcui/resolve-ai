import json
from datetime import date, datetime, time, timedelta, timezone
from uuid import uuid4

from pydantic import ValidationError

from app.support_results import EngineerEscalationPackage, EvidenceItem, SupportDiagnosis, SupportInvestigationResult
from app.tickets import TicketContext

TOOL_NAMES = {"get_customer_account", "get_event_notification_deliveries", "get_platform_status", "get_background_operation"}
INTERNAL_KNOWLEDGE_TOOL_NAME = "search_internal_knowledge"


def get_handoff_product_version(ticket: TicketContext) -> str | None:
    if ticket.handoff is None:
        return None

    direct_version = ticket.handoff.environment_snapshot.get("product_version")
    if direct_version is not None:
        return str(direct_version)

    current_product_context = ticket.handoff.environment_snapshot.get("current_product_context")
    if isinstance(current_product_context, dict) and current_product_context.get("product_version") is not None:
        return str(current_product_context["product_version"])

    return None


def create_tool_evidence(ticket_id: int, customer_id: str, tool_name: str, data: object) -> list[EvidenceItem]:
    records = data[:5] if isinstance(data, list) else [data]
    evidence: list[EvidenceItem] = []

    for record in records:
        if not isinstance(record, dict) or record.get("status") == "error":
            continue

        if tool_name != "get_platform_status" and record.get("customer_id") != customer_id:
            continue

        timestamp = record.get("attempted_at", record.get("updated_at"))
        observations: list[tuple[str, str, dict[str, str | int | bool]]] = []

        if tool_name == "get_event_notification_deliveries":
            if not isinstance(record.get("delivery_id"), str) or not isinstance(record.get("delivery_status"), str) or type(record.get("response_status")) is not int:
                continue
            facts = {"delivery_status": record["delivery_status"], "response_status": record["response_status"]}
            observations.append((record["delivery_id"], "order notifications", facts))
        elif tool_name == "get_background_operation":
            if not isinstance(record.get("operation_id"), str) or not isinstance(record.get("status"), str) or not isinstance(record.get("latest_run_status"), str) or not isinstance(record.get("failure_code"), str) or type(record.get("retry_allowed")) is not bool or record.get("feature") != "report exports":
                continue
            operation_facts = {"operation_id": record["operation_id"], "record_type": "operation", "status": record["status"], "failure_code": record["failure_code"], "retry_allowed": record["retry_allowed"]}
            latest_run_facts = {"operation_id": record["operation_id"], "record_type": "latest_run", "status": record["latest_run_status"]}
            observations.append((f"{record['operation_id']}:operation", "report exports", operation_facts))
            observations.append((f"{record['operation_id']}:latest_run", "report exports", latest_run_facts))
        elif tool_name == "get_platform_status":
            feature = {"event_notifications": "order notifications", "report_exports": "report exports"}.get(record.get("service"))
            if feature is None or not isinstance(record.get("status"), str):
                continue
            observations.append((record["service"], feature, {"service": record["service"], "status": record["status"]}))
        elif tool_name == "get_customer_account":
            if not isinstance(record.get("status"), str) or not isinstance(record.get("plan"), str) or not isinstance(record.get("product_version"), str):
                continue
            observations.append((customer_id, "account", {"status": record["status"], "plan": record["plan"], "product_version": record["product_version"]}))

        for reference, feature, facts in observations:
            try:
                item = EvidenceItem(evidence_id=f"ticket_{ticket_id}:evidence_{uuid4().hex}", ticket_id=ticket_id, customer_id=customer_id, source_reference=f"{tool_name}:{reference}", observed_at=timestamp, summary=f"{reference}: {json.dumps(facts, ensure_ascii=True, sort_keys=True)}", feature=feature, facts=facts)
            except ValidationError:
                continue
            evidence.append(item)

    return evidence


def create_internal_knowledge_evidence(ticket_id: int, customer_id: str, version: str | None, feature: str, data: object) -> list[EvidenceItem]:
    if not isinstance(data, list):
        return []

    evidence: list[EvidenceItem] = []
    current_date = date.today()

    for record in data[:3]:
        if not isinstance(record, dict):
            continue
        if record.get("visibility") != "INTERNAL" or record.get("feature") not in (feature, "all"):
            continue
        if version is not None and record.get("version") != version:
            continue
        if not isinstance(record.get("chunk_id"), str) or not isinstance(record.get("source_uri"), str) or not isinstance(record.get("content"), str):
            continue
        if not record["chunk_id"].strip() or not record["source_uri"].startswith("docs/internal/") or not record["content"].strip():
            continue
        if isinstance(record.get("score"), bool) or not isinstance(record.get("score"), (int, float)):
            continue

        try:
            effective_from = date.fromisoformat(str(record["effective_from"]))
            effective_to = date.fromisoformat(str(record["effective_to"])) if record.get("effective_to") is not None else None
        except (KeyError, ValueError):
            continue

        if effective_from > current_date or effective_to is not None and effective_to < current_date:
            continue

        observed_at = datetime.combine(effective_from, time.min, tzinfo=timezone.utc)
        facts: dict[str, str | int | float | bool] = {"chunk_id": record["chunk_id"], "source_uri": record["source_uri"], "version": str(record["version"]), "visibility": "INTERNAL", "effective_from": effective_from.isoformat(), "effective_to": effective_to.isoformat() if effective_to is not None else "", "score": float(record["score"])}
        item = EvidenceItem(evidence_id=f"ticket_{ticket_id}:document_{record['chunk_id']}", ticket_id=ticket_id, customer_id=customer_id, source_type="document", source_reference=f"{INTERNAL_KNOWLEDGE_TOOL_NAME}:{record['chunk_id']}", observed_at=observed_at, summary=record["content"][:1000], feature=str(record["feature"]), facts=facts)
        evidence.append(item)

    return evidence


def parse_source_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        return None
    return timestamp.astimezone(timezone.utc)


def problem_time_window(ticket: TicketContext) -> tuple[datetime, datetime] | None:
    if ticket.handoff is None:
        return None

    start_text = ticket.handoff.approximate_start_time
    start_time = parse_source_time(start_text)

    if start_time is not None:
        return start_time - timedelta(hours=1), start_time + timedelta(days=1)

    if isinstance(start_text, str) and start_text.strip().lower() in ("today", "this morning", "this afternoon", "this evening") and ticket.created_at is not None:
        created_at = ticket.created_at.replace(tzinfo=timezone.utc) if ticket.created_at.tzinfo is None else ticket.created_at.astimezone(timezone.utc)
        day_start = created_at.replace(hour=0, minute=0, second=0, microsecond=0)
        return day_start, day_start + timedelta(days=1)

    activity = ticket.handoff.environment_snapshot.get("recent_activity")

    if isinstance(activity, dict):
        activity_time = parse_source_time(activity.get("occurred_at"))
        if activity_time is not None:
            return activity_time - timedelta(days=1), activity_time + timedelta(days=1)

    return None


def validate_support_evidence(ticket: TicketContext, diagnosis: SupportDiagnosis, evidence: list[EvidenceItem]) -> SupportInvestigationResult:
    errors: list[str] = []
    valid_evidence: dict[str, EvidenceItem] = {}
    time_window = problem_time_window(ticket)
    product_version = get_handoff_product_version(ticket)

    for item in evidence:
        source_name = item.source_reference.split(":", 1)[0]
        if ticket.handoff is None or item.ticket_id != ticket.id or item.customer_id != ticket.handoff.customer_id:
            errors.append("Evidence does not belong to the current customer and ticket.")
        elif item.source_type == "tool" and source_name not in TOOL_NAMES:
            errors.append("Evidence has an unapproved source.")
        elif item.source_type == "document" and source_name != INTERNAL_KNOWLEDGE_TOOL_NAME:
            errors.append("Internal knowledge evidence has an unapproved source.")
        elif item.source_type == "document" and item.facts.get("visibility") != "INTERNAL":
            errors.append("Internal knowledge evidence has the wrong visibility.")
        elif item.source_type == "document" and item.source_reference != f"{INTERNAL_KNOWLEDGE_TOOL_NAME}:{item.facts.get('chunk_id')}":
            errors.append("Internal knowledge evidence does not match its document chunk.")
        elif item.source_type == "document" and product_version is not None and item.facts.get("version") != product_version:
            errors.append("Internal knowledge evidence has the wrong product version.")
        elif item.source_type == "document" and not internal_document_is_current(item):
            errors.append("Internal knowledge evidence is not currently effective.")
        elif item.source_type == "tool" and time_window is None:
            errors.append("The issue time window is unknown; evidence timing cannot be confirmed.")
        elif item.source_type == "tool" and time_window is not None and not time_window[0] <= item.observed_at <= time_window[1]:
            errors.append("Evidence is outside the current issue time window.")
        elif item.feature not in (ticket.handoff.affected_feature, "account") and not (item.source_type == "document" and item.feature == "all"):
            errors.append("Evidence concerns a different product feature.")
        elif item.evidence_id in valid_evidence:
            errors.append("Evidence IDs must be unique within the investigation.")
        else:
            valid_evidence[item.evidence_id] = item

    supporting_ids: list[str] = []
    contradicting_ids: list[str] = []

    for proposed_ids, accepted_ids in ((diagnosis.supporting_evidence_ids, supporting_ids), (diagnosis.contradicting_evidence_ids, contradicting_ids)):
        for evidence_id in proposed_ids:
            if evidence_id not in valid_evidence:
                errors.append("The diagnosis references nonexistent or invalid evidence.")
            elif evidence_id not in accepted_ids:
                accepted_ids.append(evidence_id)

    observed_records: dict[tuple[str, datetime], EvidenceItem] = {}
    for item in valid_evidence.values():
        record_key = (item.source_reference, item.observed_at)
        previous = observed_records.get(record_key)
        if previous is not None and previous.facts != item.facts:
            errors.append("The same source record has conflicting observations at the same timestamp.")
            for conflicting_item in (previous, item):
                if conflicting_item.evidence_id not in contradicting_ids:
                    contradicting_ids.append(conflicting_item.evidence_id)
        observed_records[record_key] = item

    operations: dict[str, EvidenceItem] = {}
    latest_runs: dict[str, EvidenceItem] = {}
    for item in valid_evidence.values():
        operation_id = item.facts.get("operation_id")
        if isinstance(operation_id, str) and item.facts.get("record_type") == "operation":
            operations[operation_id] = item
        elif isinstance(operation_id, str) and item.facts.get("record_type") == "latest_run":
            latest_runs[operation_id] = item

    for operation_id, operation in operations.items():
        latest_run = latest_runs.get(operation_id)
        if latest_run is not None and operation.facts["status"] != latest_run.facts["status"]:
            errors.append("The operation status conflicts with the latest run status.")
            for item in (operation, latest_run):
                if item.evidence_id not in contradicting_ids:
                    contradicting_ids.append(item.evidence_id)

    if contradicting_ids:
        errors.append("Conflicting evidence requires engineer investigation.")

    if not supporting_ids:
        errors.append("The diagnosis has no valid supporting evidence.")

    if diagnosis.outcome != "engineer_escalation":
        if product_version is None:
            errors.append("A definite outcome requires a confirmed product version for internal knowledge validation.")
        if not diagnosis.root_cause or not diagnosis.root_cause.strip() or not diagnosis.resolution or not diagnosis.resolution.strip() or diagnosis.confidence_band == "low":
            errors.append("A definite outcome requires a supported cause, resolution, and at least medium confidence.")

        cited_sources = {valid_evidence[evidence_id].source_reference.split(":", 1)[0] for evidence_id in supporting_ids}
        primary_source = "get_background_operation" if ticket.handoff is not None and ticket.handoff.affected_feature == "report exports" else "get_event_notification_deliveries"
        if not {primary_source, "get_platform_status"}.issubset(cited_sources):
            errors.append("The outcome is missing required primary-state or platform evidence.")
        if INTERNAL_KNOWLEDGE_TOOL_NAME not in cited_sources:
            errors.append("The outcome is missing a current internal knowledge citation.")

        if diagnosis.outcome == "action_required":
            retry_supported = False
            platform_operational = any(valid_evidence[evidence_id].facts.get("service") == "report_exports" and valid_evidence[evidence_id].facts.get("status") == "operational" for evidence_id in supporting_ids)
            for operation_id, operation in operations.items():
                latest_run = latest_runs.get(operation_id)
                if operation.evidence_id in supporting_ids and latest_run is not None and latest_run.evidence_id in supporting_ids and operation.facts.get("status") == "failed" and operation.facts.get("failure_code") == "dependency_timeout" and operation.facts.get("retry_allowed") is True and latest_run.facts.get("status") == "failed":
                    retry_supported = True
            if not retry_supported or not platform_operational:
                errors.append("The evidence does not support a safe internal retry requirement.")

    if diagnosis.outcome == "action_required" and diagnosis.action_proposal is None:
        errors.append("An action-required outcome must include a bounded action proposal.")

    if diagnosis.outcome != "action_required" and diagnosis.action_proposal is not None:
        errors.append("Only an action-required outcome can include an action proposal.")

    if not valid_evidence:
        errors.append("No valid internal evidence supports a reliable diagnosis.")

    values = diagnosis.model_dump(include=set(SupportDiagnosis.model_fields))
    values.update(supporting_evidence_ids=supporting_ids, contradicting_evidence_ids=contradicting_ids)
    errors = list(dict.fromkeys(errors))

    if errors:
        values.update(conclusion="The investigation could not validate a reliable diagnosis.", root_cause=None, confidence_band="low", resolution=None, escalation_reason=" ".join(errors), action_proposal=None, customer_explanation="目前的信息还不足以确认问题原因，已经交给工程师继续检查。你不需要重复说明已经提供的信息。", outcome="engineer_escalation")
    elif diagnosis.outcome == "engineer_escalation" and not diagnosis.escalation_reason:
        values["escalation_reason"] = "The available evidence does not support a safe resolution."

    internal_citation_ids = [str(valid_evidence[evidence_id].facts["chunk_id"]) for evidence_id in supporting_ids if valid_evidence[evidence_id].source_type == "document"]
    result = SupportInvestigationResult(**values, supporting_facts=[valid_evidence[evidence_id].summary for evidence_id in supporting_ids], internal_citation_ids=internal_citation_ids, evidence=list(valid_evidence.values()), validation_errors=errors)

    return result


def internal_document_is_current(item: EvidenceItem) -> bool:
    try:
        effective_from = date.fromisoformat(str(item.facts["effective_from"]))
        effective_to_text = str(item.facts["effective_to"])
        effective_to = date.fromisoformat(effective_to_text) if effective_to_text else None
    except (KeyError, ValueError):
        return False

    current_date = date.today()
    return effective_from <= current_date and (effective_to is None or effective_to >= current_date)


def build_engineer_escalation_package(ticket: TicketContext, result: SupportInvestigationResult, tool_errors: list[str], tools_used: list[str]) -> EngineerEscalationPackage:
    handoff = ticket.handoff
    excluded_causes: list[str] = []
    if any(item.facts.get("service") is not None and item.facts.get("status") == "operational" for item in result.evidence):
        excluded_causes.append("A reported platform outage at the observed source timestamp.")

    questions = handoff.remaining_questions.copy() if handoff is not None else ["The customer diagnostic handoff is missing."]
    questions.extend(result.validation_errors)
    questions.extend(tool_errors)
    if not questions:
        questions.append("Which remaining cause or missing fact prevents a safe resolution?")
    next_checks = ["Review the attached source references and investigate only the remaining gaps; do not repeat confirmed customer questions."]
    if result.contradicting_evidence_ids:
        next_checks.append("Compare the operation record with its latest run and determine which state is authoritative before considering any action.")
    if any("time" in error.lower() for error in result.validation_errors):
        next_checks.append("Confirm the issue time window and collect source records from that window.")
    if tool_errors:
        next_checks.append("Review failed or incomplete investigation steps and retrieve only the missing source records.")

    return EngineerEscalationPackage(ticket_id=ticket.id, customer_id=handoff.customer_id if handoff is not None else None, issue_summary=handoff.issue_summary if handoff is not None else "The customer diagnostic handoff is missing.", customer_impact=handoff.customer_impact if handoff is not None else "Customer impact is unknown.", customer_diagnosis=handoff, internal_evidence_ids=[item.evidence_id for item in result.evidence], tools_used=tools_used.copy(), excluded_causes=excluded_causes, possible_causes=[result.root_cause] if result.root_cause is not None and result.supporting_evidence_ids else [], unanswered_questions=list(dict.fromkeys(questions)), escalation_reason=result.escalation_reason or "No safe resolution could be confirmed.", next_checks=next_checks)
