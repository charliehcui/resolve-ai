from backend.app.database import get_connection
from backend.app.models import AuthContext
from backend.app.support_evidence import load_evidence


def get_case_id(conversation_id: str, auth: AuthContext) -> str:
    with get_connection() as connection:
        row = connection.execute("SELECT case_id::text FROM support.cases WHERE conversation_id = %s AND company_id = %s", (conversation_id, auth.company_id)).fetchone()
    if row is None:
        raise PermissionError("Support case is not available in this user scope")
    return row["case_id"]


def update_case(case_id: str, status: str, outcome: str, tool_call_count: int, total_latency_ms: int) -> None:
    with get_connection() as connection:
        connection.execute("UPDATE support.cases SET status = %s, outcome = %s, tool_call_count = %s, total_latency_ms = %s, updated_at = NOW() WHERE case_id = %s", (status, outcome, tool_call_count, total_latency_ms, case_id))


def show_case(case_id: str, auth: AuthContext) -> dict[str, object]:
    with get_connection() as connection:
        case = connection.execute("""SELECT c.case_id::text, c.conversation_id::text, c.status, c.outcome, c.tool_call_count, c.total_latency_ms,
            h.customer_problem, h.customer_answer, h.citations, h.attempted_steps, h.known_shop_id, h.known_order_id, h.known_sku, h.missing_fields
            FROM support.cases c JOIN support.handoffs h ON h.handoff_id = c.handoff_id JOIN support.conversations v ON v.conversation_id = c.conversation_id
            WHERE c.case_id = %s AND c.company_id = %s AND v.user_id = %s""", (case_id, auth.company_id, auth.user_id)).fetchone()
    if case is None:
        raise PermissionError("Support case is not available in this user scope")
    result = dict(case)
    result["evidence"] = [record.model_dump() for record in load_evidence(case_id)]
    return result


