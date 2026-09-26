import json
import re
from uuid import uuid4

from pydantic import BaseModel, Field

from backend.app.database import get_connection
from backend.app.models import UserContext


#把 Customer Agent 处理不了的问题整理成一份 Support Agent 可以接手的记录，并把这段聊天切换到 Support
class SupportHandoffRecord(BaseModel):  # Customer Agent 正式交给 Support Agent 的记录
    handoff_id: str  # Customer Agent 交给 Support Agent 的交接记录 ID
    conversation_id: str  # 这次交接所属的对话 ID
    company_id: str
    customer_problem: str
    attempted_steps: list[str] = Field(default_factory=list)
    known_shop_id: str | None = None
    known_order_id: str | None = None
    known_sku: str | None = None
    missing_fields: list[str] = Field(default_factory=list)


def extract_support_ids(text: str) -> tuple[str | None, str | None, str | None]:   #从用户文字里直接找 shop_id、order_id 和 sku
    shop_match = re.search(r"\bshop-[A-Za-z0-9-]+\b", text, re.IGNORECASE)
    order_match = re.search(r"\b(?:O|ORDER)-[A-Za-z0-9-]+\b", text, re.IGNORECASE)
    sku_match = re.search(r"\bSKU-[A-Za-z0-9-]+\b", text, re.IGNORECASE)

    shop_id = shop_match.group(0) if shop_match is not None else None
    order_id = order_match.group(0) if order_match is not None else None
    sku = sku_match.group(0) if sku_match is not None else None

    return shop_id, order_id, sku


def find_missing_support_ids(shop_id: str | None, order_id: str | None, sku: str | None, text: str) -> list[str]: #判断 Support Agent 现在还缺什么重要编号
    text_lower = text.lower()
    is_stock_question = "库存" in text or "stock" in text_lower or "sku" in text_lower

    missing_fields: list[str] = []

    if shop_id is None:
        missing_fields.append("shop_id")

    if is_stock_question is True:
        if sku is None:
            missing_fields.append("sku")
    else:
        if order_id is None:
            missing_fields.append("order_id")

    return missing_fields


def find_attempted_steps(history: list[dict[str, object]], question: str) -> list[str]:  #找出用户之前已经尝试过什么
    attempt_keywords = ["试过", "尝试", "仍然", "还是", "没有解决", "未解决"]
    attempted_steps: list[str] = []

    for message in history[-6:]:
        if message["role"] == "user":
            message_text = str(message["content"])
            describes_attempt = any(keyword in message_text for keyword in attempt_keywords)
            is_duplicate = message_text in attempted_steps

            if describes_attempt is True and is_duplicate is False:
                attempted_steps.append(message_text)

    question_describes_attempt = any(keyword in question for keyword in attempt_keywords)
    question_is_duplicate = question in attempted_steps

    if question_describes_attempt is True and question_is_duplicate is False:
        attempted_steps.append(question)

    return attempted_steps

#真正执行 Customer → Support 交接
def create_support_handoff(user: UserContext, conversation_id: str, question: str, history: list[dict[str, object]]) -> tuple[SupportHandoffRecord, str]:
    shop_id, order_id, sku = extract_support_ids(question)
    attempted_steps = find_attempted_steps(history, question)
    missing_fields = find_missing_support_ids(shop_id, order_id, sku, question)

    handoff_id = str(uuid4())
    case_id = str(uuid4())

    with get_connection() as connection:
        existing = connection.execute(
            """SELECT h.handoff_id::text, c.case_id::text
            FROM support.handoffs h
            JOIN support.cases c ON c.handoff_id = h.handoff_id
            WHERE h.conversation_id = %s AND h.company_id = %s""",
            (conversation_id, user.company_id),
        ).fetchone()

        if existing is not None:
            existing_handoff = get_support_handoff(conversation_id, user)
            return existing_handoff, existing["case_id"]

        conversation = connection.execute(
            """SELECT company_id, user_id
            FROM support.conversations
            WHERE conversation_id = %s
            FOR UPDATE""",
            (conversation_id,),
        ).fetchone()

        if conversation is None:
            raise PermissionError("Conversation is not available in this user scope")

        if conversation["company_id"] != user.company_id or conversation["user_id"] != user.user_id:
            raise PermissionError("Conversation is not available in this user scope")

        connection.execute(
            """INSERT INTO support.handoffs
            (handoff_id, conversation_id, company_id, customer_problem, attempted_steps,
            known_shop_id, known_order_id, known_sku, missing_fields)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb)""",
            (
                handoff_id,
                conversation_id,
                user.company_id,
                question,
                json.dumps(attempted_steps, ensure_ascii=False),
                shop_id,
                order_id,
                sku,
                json.dumps(missing_fields),
            ),
        )

        connection.execute(
            """INSERT INTO support.cases
            (case_id, conversation_id, handoff_id, company_id)
            VALUES (%s, %s, %s, %s)""",
            (
                case_id,
                conversation_id,
                handoff_id,
                user.company_id,
            ),
        )

        connection.execute(
            """UPDATE support.conversations
            SET active_role = 'SUPPORT', updated_at = NOW()
            WHERE conversation_id = %s AND company_id = %s""",
            (
                conversation_id,
                user.company_id,
            ),
        )

    return get_support_handoff(conversation_id, user), case_id


def get_support_handoff(conversation_id: str, user: UserContext) -> SupportHandoffRecord:   #从数据库把已经保存的 Handoff 重新读取出来
    with get_connection() as connection:
        row = connection.execute(
            """SELECT handoff_id::text, conversation_id::text, company_id, customer_problem,
            attempted_steps, known_shop_id, known_order_id, known_sku, missing_fields
            FROM support.handoffs
            WHERE conversation_id = %s AND company_id = %s""",
            (
                conversation_id,
                user.company_id,
            ),
        ).fetchone()

    if row is None:
        raise PermissionError("Support handoff is not available in this user scope")

    return SupportHandoffRecord.model_validate(row)


def update_support_ids(conversation_id: str, user: UserContext, text: str) -> SupportHandoffRecord:
    new_shop_id, new_order_id, new_sku = extract_support_ids(text)

    with get_connection() as connection:
        row = connection.execute(
            """SELECT known_shop_id, known_order_id, known_sku, customer_problem
            FROM support.handoffs
            WHERE conversation_id = %s AND company_id = %s
            FOR UPDATE""",
            (
                conversation_id,
                user.company_id,
            ),
        ).fetchone()

        if row is None:
            raise PermissionError("Support handoff is not available in this user scope")

        shop_id = new_shop_id if new_shop_id is not None else row["known_shop_id"]
        order_id = new_order_id if new_order_id is not None else row["known_order_id"]
        sku = new_sku if new_sku is not None else row["known_sku"]

        full_problem_text = f"{row['customer_problem']} {text}"
        missing_fields = find_missing_support_ids(shop_id, order_id, sku, full_problem_text)

        connection.execute(
            """UPDATE support.handoffs
            SET known_shop_id = %s,
                known_order_id = %s,
                known_sku = %s,
                missing_fields = %s::jsonb,
                updated_at = NOW()
            WHERE conversation_id = %s AND company_id = %s""",
            (
                shop_id,
                order_id,
                sku,
                json.dumps(missing_fields),
                conversation_id,
                user.company_id,
            ),
        )

    return get_support_handoff(conversation_id, user)


# Customer Agent 判断：
# 这个问题需要真实订单 / 库存 / 店铺数据
# ↓
# create_support_handoff()
# ↓
# 从用户问题里找 shop_id / order_id / sku
# ↓
# 记录用户已经试过什么
# ↓
# 记录还缺什么信息
# ↓
# 创建 Handoff
# ↓
# 创建 Support Case
# ↓
# 把 conversation 的 active_role 改成 SUPPORT
# ↓
# 下一条消息开始交给 Support Agent
