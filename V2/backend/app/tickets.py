import json
import re
from typing import Literal
from uuid import uuid4

from backend.app.auth import authorize_conversation
from backend.app.database import get_connection
from backend.app.models import UserContext
from backend.app.support_evidence import EvidenceRecord
from backend.app.support_tools import execute_tool_batch
from backend.app.verification import verify_order_facts, verify_shipment_facts, verify_stock_facts

TicketTrigger = Literal["support_unresolved", "budget_reached", "evidence_insufficient", "unknown_error", "user_requested"]
TicketRecheckStatus = Literal["RESOLVED", "UNRESOLVED", "NEEDS_INFO"]
OPEN_TICKET_STATUSES = ("open", "in_progress")
PRODUCT_VERSION = "2.0"


def rows_as_dicts(rows) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for row in rows:
        items.append(dict(row))
    return items


def records_by_tool(records: list[EvidenceRecord]) -> dict[str, EvidenceRecord]:
    indexed_records: dict[str, EvidenceRecord] = {}
    for record in records:
        indexed_records[record.tool_name] = record
    return indexed_records


def record_evidence_ids(records: list[EvidenceRecord]) -> list[str]:
    evidence_ids: list[str] = []
    for record in records:
        evidence_ids.append(record.evidence_id)
    return evidence_ids


def is_human_request(text: str) -> bool:
    return bool(re.search(r"人工|工程师|真人|human support|engineer", text, re.IGNORECASE))


def ticket_category(known_order_id: str | None, known_sku: str | None, evidence: list[dict[str, object]]) -> str:
    if known_sku:
        return "stock"

    for item in evidence:
        if "Shipment" in str(item["tool_name"]):
            return "shipment"

    if known_order_id:
        return "order"
    return "general"


def excluded_causes(evidence: list[dict[str, object]]) -> list[dict[str, object]]:
    excluded = []
    for item in evidence:
        response = item["response"]
        if item["status"] != "success" or not isinstance(response, dict):
            continue
        if item["tool_name"] == "GetShopStatus" and response.get("sync_enabled") is True:
            excluded.append({"reason": "ORDER_SYNC_DISABLED", "evidence_id": item["evidence_id"]})
        if item["tool_name"] == "CheckConnection" and response.get("connection_status") == "authorized":
            excluded.append({"reason": "CONNECTION_NOT_AUTHORIZED", "evidence_id": item["evidence_id"]})
        if item["tool_name"] == "GetOrder" and response.get("event_id"):
            excluded.append({"reason": "SOURCE_ORDER_MISSING", "evidence_id": item["evidence_id"]})
        if item["tool_name"] == "GetShipment" and response.get("shipment_id"):
            excluded.append({"reason": "WAREHOUSE_SHIPMENT_MISSING", "evidence_id": item["evidence_id"]})
    return excluded


def next_steps_for(category: str, failed_evidence: list[dict[str, object]], unknowns: list[str]) -> list[str]:
    steps = []
    if failed_evidence:
        steps.append("Inspect the recorded request IDs and service errors without repeating a write operation.")
    if category == "order":
        steps.append("Compare the latest platform order, merchant task, shop state, and SKU mapping.")
    elif category == "shipment":
        steps.append("Compare warehouse shipment, merchant forwarding task, and platform shipment using the same shipment ID.")
    elif category == "stock":
        steps.append("Compare mapping, warehouse source version, publish task, platform version, and propagation time.")
    else:
        steps.append("Clarify the requested business target before accessing additional business facts.")
    if unknowns:
        steps.append("Resolve the listed unknowns before proposing any business write.")
    return steps


def load_ticket_source(conversation_id: str) -> dict[str, object]:
    with get_connection() as connection:
        source = connection.execute("""SELECT v.conversation_id::text, v.company_id, v.user_id, v.active_role,
            c.case_id::text, c.status AS case_status, c.outcome, h.handoff_id::text, h.customer_problem, h.customer_answer,
            h.attempted_steps, h.known_shop_id, h.known_order_id, h.known_sku, h.missing_fields
            FROM support.conversations v
            LEFT JOIN support.cases c ON c.conversation_id = v.conversation_id
            LEFT JOIN support.handoffs h ON h.conversation_id = v.conversation_id
            WHERE v.conversation_id = %s""", (conversation_id,)).fetchone()

        evidence = []
        actions = []
        action_steps = []
        if source is not None and source["case_id"] is not None:
            case_id = source["case_id"]
            evidence = connection.execute("""SELECT evidence_id::text, sequence, tool_name, request, response, source_service, source_record_id, status, object_type, object_id, observed_at, source_version
                FROM support.evidence WHERE case_id = %s ORDER BY sequence""", (case_id,)).fetchall()
            actions = connection.execute("""SELECT p.action_id::text, p.action_type, p.status, p.shop_id, p.external_order_id, p.created_at,
                d.decision, d.decided_by, d.decided_at, v.status AS verification_status, v.details AS verification_details
                FROM support.action_proposals p LEFT JOIN support.action_decisions d ON d.action_id = p.action_id
                LEFT JOIN support.action_verifications v ON v.action_id = p.action_id WHERE p.case_id = %s ORDER BY p.created_at""", (case_id,)).fetchall()
            action_steps = connection.execute("""SELECT s.action_id::text, s.step_name, s.status, s.details, s.evidence_id::text, s.created_at
                FROM support.action_steps s JOIN support.action_proposals p ON p.action_id = s.action_id WHERE p.case_id = %s ORDER BY s.created_at""", (case_id,)).fetchall()

    source_data = dict(source) if source else None
    return {
        "source": source_data,
        "evidence": rows_as_dicts(evidence),
        "actions": rows_as_dicts(actions),
        "action_steps": rows_as_dicts(action_steps),
    }


def create_ticket(user: UserContext, conversation_id: str, trigger: TicketTrigger, reason: str) -> dict[str, object]:
    if user.role == "engineer":
        raise PermissionError("Engineer identities cannot create merchant tickets")
    authorize_conversation(user, conversation_id)
    data = load_ticket_source(conversation_id)
    source = data["source"]
    if source is None:
        raise PermissionError("Conversation is not available in this user scope")
    with get_connection() as connection:
        existing = connection.execute("SELECT ticket_id::text FROM support.tickets WHERE conversation_id = %s AND status IN ('open', 'in_progress')", (conversation_id,)).fetchone()
    if existing:
        result = show_ticket(user, existing["ticket_id"])
        result["duplicate"] = True
        return result
    unresolved_action = False
    for action in data["actions"]:
        if action["status"] in {"blocked", "verification_failed", "expired"}:
            unresolved_action = True
            break

    if trigger != "user_requested" and source["case_status"] != "pending_human" and not unresolved_action:
        raise ValueError("Ticket creation requires an unresolved case, a failed action, or an explicit human request")

    evidence = data["evidence"]
    failed_evidence: list[dict[str, object]] = []
    confirmed: list[dict[str, object]] = []

    for item in evidence:
        if item["status"] in {"forbidden", "unavailable", "error"}:
            failed_evidence.append(item)
        elif item["status"] in {"success", "empty", "not_found"}:
            confirmed.append(
                {
                    "evidence_id": item["evidence_id"],
                    "tool_name": item["tool_name"],
                    "source_service": item["source_service"],
                    "status": item["status"],
                    "object_type": item["object_type"],
                    "object_id": item["object_id"],
                    "source_version": item["source_version"],
                }
            )

    possible: list[dict[str, object]] = []
    for item in failed_evidence:
        response = item["response"]
        if isinstance(response, dict) and response.get("error_code"):
            possible.append({"evidence_id": item["evidence_id"], "error_code": response.get("error_code")})

    unknowns = list(source["missing_fields"] or [])
    for item in failed_evidence:
        unknowns.append(f"{item['tool_name']} did not return a usable fact ({item['status']}).")

    if not evidence:
        unknowns.append("No business evidence has been collected yet.")

    category = ticket_category(source["known_order_id"], source["known_sku"], evidence)
    business_target = {"shop_id": source["known_shop_id"], "order_id": source["known_order_id"], "sku": source["known_sku"]}

    successful_steps: list[dict[str, object]] = []
    for item in data["action_steps"]:
        if item["status"] in {"passed", "verified_resolved"}:
            successful_steps.append(item)

    if successful_steps:
        latest_step = successful_steps[-1]
        last_successful_step = {
            "action_id": latest_step["action_id"],
            "step_name": latest_step["step_name"],
            "status": latest_step["status"],
            "evidence_id": latest_step["evidence_id"],
            "created_at": latest_step["created_at"],
        }
    else:
        successful_evidence: list[dict[str, object]] = []
        for item in evidence:
            if item["status"] == "success":
                successful_evidence.append(item)

        if successful_evidence:
            latest_evidence = successful_evidence[-1]
            last_successful_step = {
                "evidence_id": latest_evidence["evidence_id"],
                "tool_name": latest_evidence["tool_name"],
                "observed_at": latest_evidence["observed_at"],
            }
        else:
            last_successful_step = None
    attempted_actions = [*list(source["attempted_steps"] or []), *data["actions"], *data["action_steps"]]
    customer_problem = source["customer_problem"] or reason
    current_result = source["outcome"] or reason
    ticket_id = str(uuid4())
    with get_connection() as connection:
        assignment = connection.execute("""SELECT r.engineer_user_id FROM support.ticket_assignment_rules r JOIN support.users u ON u.user_id = r.engineer_user_id
            WHERE r.company_id = %s AND r.active AND u.role = 'engineer' AND u.company_id = %s""", (user.company_id, user.company_id)).fetchone()
        if assignment:
            assigned_to = assignment["engineer_user_id"]
        else:
            assigned_to = None

        failed_evidence_ids: list[str] = []
        for item in failed_evidence:
            failed_evidence_ids.append(str(item["evidence_id"]))

        connection.execute("""INSERT INTO support.tickets (ticket_id, conversation_id, case_id, handoff_id, company_id, category, trigger, product_version, customer_problem, business_target,
            last_successful_step, failed_evidence_ids, attempted_actions, confirmed_facts, possible_causes, excluded_causes, unknowns, next_steps, current_result, created_by, assigned_to)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s)""",
            (ticket_id, conversation_id, source["case_id"], source["handoff_id"], user.company_id, category, trigger, PRODUCT_VERSION, customer_problem, json.dumps(business_target), json.dumps(last_successful_step, default=str) if last_successful_step else None, json.dumps(failed_evidence_ids), json.dumps(attempted_actions, default=str), json.dumps(confirmed, default=str), json.dumps(possible, default=str), json.dumps(excluded_causes(evidence)), json.dumps(unknowns), json.dumps(next_steps_for(category, failed_evidence, unknowns)), current_result, user.user_id, assigned_to),
        )
        if assigned_to:
            connection.execute("INSERT INTO support.ticket_read_grants (ticket_id, user_id) VALUES (%s, %s)", (ticket_id, assigned_to))
    result = show_ticket(user, ticket_id)
    result["duplicate"] = False
    return result


def show_ticket(user: UserContext, ticket_id: str) -> dict[str, object]:
    with get_connection() as connection:
        if user.role == "engineer":
            ticket = connection.execute("""SELECT t.* FROM support.tickets t JOIN support.ticket_read_grants g ON g.ticket_id = t.ticket_id
                WHERE t.ticket_id = %s AND g.user_id = %s""", (ticket_id, user.user_id)).fetchone()
        else:
            ticket = connection.execute("""SELECT t.* FROM support.tickets t JOIN support.conversations v ON v.conversation_id = t.conversation_id
                WHERE t.ticket_id = %s AND t.company_id = %s AND v.user_id = %s""", (ticket_id, user.company_id, user.user_id)).fetchone()
        if ticket is None:
            raise PermissionError("Ticket is not available in this user scope")
        messages = connection.execute("SELECT message_id::text, role, content, metadata, created_at FROM support.messages WHERE conversation_id = %s ORDER BY created_at", (ticket["conversation_id"],)).fetchall()

        handoff = None
        if ticket["handoff_id"] is not None:
            handoff = connection.execute("""SELECT handoff_id::text, conversation_id::text, customer_problem, customer_answer, citations, attempted_steps,
                known_shop_id, known_order_id, known_sku, unresolved_reason, missing_fields, created_at, updated_at FROM support.handoffs WHERE handoff_id = %s""", (ticket["handoff_id"],)).fetchone()

        evidence = []
        if ticket["case_id"] is not None:
            evidence = connection.execute("SELECT evidence_id::text, sequence, tool_name, request, response, source_service, status, object_type, object_id, observed_at, source_version FROM support.evidence WHERE case_id = %s ORDER BY sequence", (ticket["case_id"],)).fetchall()

        rechecks = connection.execute("SELECT recheck_id::text, status, details, evidence_ids, checked_by, checked_at FROM support.ticket_rechecks WHERE ticket_id = %s ORDER BY checked_at", (ticket_id,)).fetchall()

    result = dict(ticket)
    for field in ("ticket_id", "conversation_id", "case_id", "handoff_id"):
        field_value = result[field]
        if field_value:
            result[field] = str(field_value)
        else:
            result[field] = None

    result["messages"] = rows_as_dicts(messages)
    if handoff:
        result["handoff"] = dict(handoff)
    else:
        result["handoff"] = None
    result["evidence"] = rows_as_dicts(evidence)
    result["rechecks"] = rows_as_dicts(rechecks)

    timeline: list[dict[str, object]] = []
    for message in result["messages"]:
        timeline.append(
            {
                "type": "message",
                "id": message["message_id"],
                "status": message["role"],
                "at": message["created_at"],
                "text": message["content"],
            }
        )

    for item in result["evidence"]:
        timeline.append(
            {
                "type": "evidence",
                "id": item["evidence_id"],
                "status": item["status"],
                "at": item["observed_at"],
                "text": item["tool_name"],
            }
        )

    for recheck in result["rechecks"]:
        recheck_details = recheck["details"]
        recheck_text = recheck_details.get("summary", recheck["status"])
        timeline.append(
            {
                "type": "recheck",
                "id": recheck["recheck_id"],
                "status": recheck["status"],
                "at": recheck["checked_at"],
                "text": recheck_text,
            }
        )

    timeline.append(
        {
            "type": "ticket",
            "id": result["ticket_id"],
            "status": result["status"],
            "at": result["created_at"],
            "text": result["current_result"],
        }
    )
    timeline.sort(key=lambda item: item["at"])
    result["timeline"] = timeline
    return result


def save_ticket_recheck(user: UserContext, ticket: dict[str, object], status: TicketRecheckStatus, details: dict[str, object], evidence_ids: list[str]) -> dict[str, object]:
    recheck_id = str(uuid4())
    ticket_status = "closed" if status == "RESOLVED" else "open"
    summary = str(details["summary"])
    with get_connection() as connection:
        connection.execute("INSERT INTO support.ticket_rechecks (recheck_id, ticket_id, status, details, evidence_ids, checked_by) VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s)", (recheck_id, ticket["ticket_id"], status, json.dumps(details, default=str), json.dumps(evidence_ids), user.user_id))
        connection.execute("UPDATE support.tickets SET status = %s, current_result = %s, updated_at = NOW() WHERE ticket_id = %s", (ticket_status, summary, ticket["ticket_id"]))
    result = show_ticket(user, str(ticket["ticket_id"]))
    result["recheck_status"] = status
    result["recheck_id"] = recheck_id
    return result


def recheck_ticket(user: UserContext, ticket_id: str) -> dict[str, object]:
    if user.role != "engineer":
        raise PermissionError("Engineer role is required to recheck a ticket")
    ticket = show_ticket(user, ticket_id)
    target = ticket["business_target"] if isinstance(ticket["business_target"], dict) else {}
    shop_id = str(target.get("shop_id") or "")
    order_id = str(target.get("order_id") or "")
    sku = str(target.get("sku") or "")
    case_id = str(ticket["case_id"] or "")
    category = str(ticket["category"])

    case_id_is_missing = not case_id
    shop_id_is_missing = not shop_id
    order_id_is_missing = category in {"order", "shipment"} and not order_id
    sku_is_missing = category == "stock" and not sku
    business_target_is_missing = category == "general"
    target_is_incomplete = (
        case_id_is_missing
        or shop_id_is_missing
        or order_id_is_missing
        or sku_is_missing
        or business_target_is_missing
    )

    if target_is_incomplete:
        missing = []
        if case_id_is_missing:
            missing.append("case_id")
        if shop_id_is_missing:
            missing.append("shop_id")
        if order_id_is_missing:
            missing.append("order_id")
        if sku_is_missing:
            missing.append("sku")
        if business_target_is_missing:
            missing.append("business_target")

        details = {"category": category, "target": target, "missing": sorted(set(missing)), "summary": "Ticket remains open because the business target is incomplete."}
        return save_ticket_recheck(user, ticket, "NEEDS_INFO", details, [])

    if category == "order":
        calls = [
            {"name": "GetOrder", "args": {"shop_id": shop_id, "order_id": order_id}, "id": "ticket-recheck-platform-order"},
            {"name": "GetProcessRecords", "args": {"shop_id": shop_id, "order_id": order_id}, "id": "ticket-recheck-merchant-order"},
        ]
        evidence = execute_tool_batch(case_id, user, calls, shop_id, order_id)
        facts = records_by_tool(evidence)
        verification = verify_order_facts(facts["GetOrder"], facts["GetProcessRecords"], facts["GetOrder"].response)
    elif category == "shipment":
        calls = [
            {"name": "GetShipment", "args": {"shop_id": shop_id, "order_id": order_id}, "id": "ticket-recheck-warehouse-shipment"},
            {"name": "GetShipmentRecords", "args": {"shop_id": shop_id, "order_id": order_id}, "id": "ticket-recheck-merchant-shipment"},
            {"name": "GetPlatformShipment", "args": {"shop_id": shop_id, "order_id": order_id}, "id": "ticket-recheck-platform-shipment"},
        ]
        evidence = execute_tool_batch(case_id, user, calls, shop_id, order_id)
        facts = records_by_tool(evidence)
        verification = verify_shipment_facts(facts["GetShipment"], facts["GetShipmentRecords"], facts["GetPlatformShipment"], facts["GetShipment"].response)
    else:
        calls = [{"name": "GetStockFacts", "args": {"shop_id": shop_id, "sku": sku}, "id": "ticket-recheck-stock"}]
        evidence = execute_tool_batch(case_id, user, calls, shop_id, "", sku)
        stock = evidence[0]
        response = stock.response
        merchant = response.get("merchant") if isinstance(response.get("merchant"), dict) else {}
        warehouse = response.get("warehouse") if isinstance(response.get("warehouse"), dict) else None
        platform = response.get("platform") if isinstance(response.get("platform"), dict) else None
        stock_result = verify_stock_facts(merchant, warehouse, platform)
        verification = {"resolved": stock.status == "success" and stock_result["assessment"] == "consistent", "stock": stock_result}
        if stock_result["assessment"] == "insufficient_information":
            details = {"category": category, "target": target, "verification": verification, "summary": "Ticket remains open because current stock facts are incomplete."}
            evidence_ids = record_evidence_ids(evidence)
            return save_ticket_recheck(user, ticket, "NEEDS_INFO", details, evidence_ids)

    resolved = verification["resolved"] is True
    if resolved:
        summary = "Ticket closed after deterministic business verification passed."
        recheck_status = "RESOLVED"
    else:
        summary = "Ticket remains open because deterministic business verification did not pass."
        recheck_status = "UNRESOLVED"

    details = {"category": category, "target": target, "verification": verification, "summary": summary}
    evidence_ids = record_evidence_ids(evidence)
    return save_ticket_recheck(user, ticket, recheck_status, details, evidence_ids)


def list_engineer_tickets(user: UserContext) -> list[dict[str, object]]:
    if user.role != "engineer":
        raise PermissionError("Engineer role is required")
    with get_connection() as connection:
        rows = connection.execute("""SELECT t.ticket_id::text, t.company_id, t.category, t.trigger, t.status, t.customer_problem, t.assigned_to, t.created_at
            FROM support.tickets t JOIN support.ticket_read_grants g ON g.ticket_id = t.ticket_id
            WHERE g.user_id = %s ORDER BY t.created_at DESC""", (user.user_id,)).fetchall()
    return rows_as_dicts(rows)
