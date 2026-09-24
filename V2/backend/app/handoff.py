import json
import re
from uuid import uuid4

from pydantic import BaseModel, Field

from backend.app.database import get_connection
from backend.app.models import Citation, UserContext


class SupportHandoff(BaseModel):
    handoff_id: str
    conversation_id: str
    company_id: str
    customer_problem: str
    customer_answer: str
    citations: list[Citation] = Field(default_factory=list)
    attempted_steps: list[str] = Field(default_factory=list)
    known_shop_id: str | None = None
    known_order_id: str | None = None
    known_sku: str | None = None
    unresolved_reason: str
    missing_fields: list[str] = Field(default_factory=list)


def extract_identifiers(text: str) -> tuple[str | None, str | None, str | None]:
    shop_match = re.search(r"\bshop-[A-Za-z0-9-]+\b", text, re.IGNORECASE)
    order_match = re.search(r"\b(?:O|ORDER)-[A-Za-z0-9-]+\b", text, re.IGNORECASE)
    sku_match = re.search(r"\bSKU-[A-Za-z0-9-]+\b", text, re.IGNORECASE)

    shop_id = shop_match.group(0) if shop_match else None
    order_id = order_match.group(0) if order_match else None
    sku = sku_match.group(0) if sku_match else None
    return shop_id, order_id, sku


def missing_identifiers(shop_id: str | None, order_id: str | None, sku: str | None, text: str = "") -> list[str]:
    stock_question = bool(re.search(r"库存|\bstock\b|\bsku\b", text, re.IGNORECASE))

    needs_sku = (sku is not None or stock_question) and order_id is None
    if needs_sku:
        subject_name = "sku"
        subject_value = sku
    else:
        subject_name = "order_id"
        subject_value = order_id

    missing_fields: list[str] = []
    if shop_id is None:
        missing_fields.append("shop_id")
    if subject_value is None:
        missing_fields.append(subject_name)

    return missing_fields


def handoff_to_support(user: UserContext, conversation_id: str, question: str, customer_answer: str, history: list[dict[str, object]]) -> tuple[SupportHandoff, str]:
    shop_id, order_id, sku = extract_identifiers(question)
    previous_answer = ""
    citations: list[dict[str, str]] = []
    for message in reversed(history):
        if message["role"] == "assistant":
            previous_answer = str(message["content"])
            metadata = message.get("metadata") or {}
            citations = list(metadata.get("citations") or [])
            break

    attempted_steps: list[str] = []
    for message in history[-6:]:
        message_text = str(message["content"])
        is_user_message = message["role"] == "user"
        describes_attempt = re.search(r"试过|尝试|仍然|还是|没有解决|未解决", message_text)
        if is_user_message and describes_attempt:
            attempted_steps.append(message_text)

    if re.search(r"试过|尝试|仍然|还是|没有解决|未解决", question):
        attempted_steps.append(question)

    missing_fields = missing_identifiers(shop_id, order_id, sku, question)
    handoff_id = str(uuid4())
    case_id = str(uuid4())
    with get_connection() as connection:
        existing = connection.execute(
            """SELECT h.handoff_id::text, c.case_id::text FROM support.handoffs h JOIN support.cases c ON c.handoff_id = h.handoff_id
            WHERE h.conversation_id = %s AND h.company_id = %s""",
            (conversation_id, user.company_id),
        ).fetchone()
        if existing:
            existing_handoff = load_handoff(conversation_id, user)
            return existing_handoff, existing["case_id"]
        conversation = connection.execute("SELECT company_id, user_id FROM support.conversations WHERE conversation_id = %s FOR UPDATE", (conversation_id,)).fetchone()
        if conversation is None or conversation["company_id"] != user.company_id or conversation["user_id"] != user.user_id:
            raise PermissionError("Conversation is not available in this user scope")
        connection.execute(
            """INSERT INTO support.handoffs (handoff_id, conversation_id, company_id, customer_problem, customer_answer, citations, attempted_steps, known_shop_id, known_order_id, known_sku, unresolved_reason, missing_fields)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s::jsonb)""",
            (handoff_id, conversation_id, user.company_id, question, previous_answer or customer_answer, json.dumps(citations), json.dumps(attempted_steps, ensure_ascii=False), shop_id, order_id, sku, question, json.dumps(missing_fields)),
        )
        connection.execute("INSERT INTO support.cases (case_id, conversation_id, handoff_id, company_id) VALUES (%s, %s, %s, %s)", (case_id, conversation_id, handoff_id, user.company_id))
        connection.execute("UPDATE support.conversations SET active_role = 'SUPPORT', updated_at = NOW() WHERE conversation_id = %s", (conversation_id,))
    return load_handoff(conversation_id, user), case_id


def load_handoff(conversation_id: str, user: UserContext) -> SupportHandoff:
    with get_connection() as connection:
        row = connection.execute(
            """SELECT handoff_id::text, conversation_id::text, company_id, customer_problem, customer_answer, citations, attempted_steps,
            known_shop_id, known_order_id, known_sku, unresolved_reason, missing_fields FROM support.handoffs
            WHERE conversation_id = %s AND company_id = %s""",
            (conversation_id, user.company_id),
        ).fetchone()
    if row is None:
        raise PermissionError("Support handoff is not available in this user scope")
    return SupportHandoff(**row)


def update_handoff_identifiers(conversation_id: str, user: UserContext, text: str) -> SupportHandoff:
    shop_id, order_id, sku = extract_identifiers(text)
    with get_connection() as connection:
        row = connection.execute(
            "SELECT known_shop_id, known_order_id, known_sku, customer_problem FROM support.handoffs WHERE conversation_id = %s AND company_id = %s FOR UPDATE",
            (conversation_id, user.company_id),
        ).fetchone()
        if row is None:
            raise PermissionError("Support handoff is not available in this user scope")
        known_shop_id = shop_id or row["known_shop_id"]
        known_order_id = order_id or row["known_order_id"]
        known_sku = sku or row["known_sku"]
        missing_fields = missing_identifiers(known_shop_id, known_order_id, known_sku, f"{row['customer_problem']} {text}")
        connection.execute(
            "UPDATE support.handoffs SET known_shop_id = %s, known_order_id = %s, known_sku = %s, missing_fields = %s::jsonb, updated_at = NOW() WHERE conversation_id = %s",
            (known_shop_id, known_order_id, known_sku, json.dumps(missing_fields), conversation_id),
        )
    return load_handoff(conversation_id, user)
