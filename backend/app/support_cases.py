from backend.app.database import get_connection
from backend.app.models import UserContext
from backend.app.support_evidence import load_evidence


#管理一整个 Support 调查 Case 的“总状态”，并且把这个 Case 和它的 Evidence 一起展示出来
#Case 是这一整次调查的“总档案”  是非常方便的“完整调查记录”
def get_case_id(conversation_id: str, user: UserContext) -> str:
    with get_connection() as connection:
        row = connection.execute("SELECT case_id::text FROM support.cases WHERE conversation_id = %s AND company_id = %s", (conversation_id, user.company_id)).fetchone()
    if row is None:
        raise PermissionError("Support case is not available in this user scope")
    return row["case_id"]
#case_id 是把整个调查串起来的编号
#调查结束以后，把 Case 的最终结果写回数据库
def update_case(case_id: str, status: str, outcome: str, tool_call_count: int, total_latency_ms: int) -> None:
    with get_connection() as connection:
        connection.execute("UPDATE support.cases SET status = %s, outcome = %s, tool_call_count = %s, total_latency_ms = %s, updated_at = NOW() WHERE case_id = %s", (status, outcome, tool_call_count, total_latency_ms, case_id))

#把整个 Support Case 完整拿出来看
def show_case(case_id: str, user: UserContext) -> dict[str, object]:
    with get_connection() as connection:
        case = connection.execute("""SELECT c.case_id::text, c.conversation_id::text, c.status, c.outcome, c.tool_call_count, c.total_latency_ms,
            h.customer_problem, h.attempted_steps, h.known_shop_id, h.known_order_id, h.known_sku, h.missing_fields
            FROM support.cases c JOIN support.handoffs h ON h.handoff_id = c.handoff_id JOIN support.conversations v ON v.conversation_id = c.conversation_id
            WHERE c.case_id = %s AND c.company_id = %s AND v.user_id = %s""", (case_id, user.company_id, user.user_id)).fetchone()
    if case is None:
        raise PermissionError("Support case is not available in this user scope")
    result = dict(case)
    result["evidence"] = [record.model_dump() for record in load_evidence(case_id)]
    return result


# support_cases.py
# 【管理整个 Support Case 的状态，并可以把 Case + Evidence 一起读取出来】
#
#
# Conversation
# ↓
# Handoff
# 【Customer Agent 把问题和已知信息交给 Support Agent】
# ↓
# Support Case
# 【正式的一次调查记录】
# ↓
#
# get_case_id()
# 【根据 conversation_id 找到这次调查对应的 case_id】
# ↓
#
# case_id
# 【把后面的 Evidence / Action / Verification 全部串起来】
#
#
# 调查过程中：
#
# Case
# ↓
# Evidence 1
# Evidence 2
# Evidence 3
# ...
#
#
# 调查得到结果以后：
#
# update_case()
# 【更新整个 Case 的最终状态】
# ↓
#
# 保存：
# status
# outcome
# tool_call_count
# total_latency_ms
#
#
# 如果以后想查看整次调查：
#
# show_case()
# 【读取 Case 基本信息】
# ↓
# load_evidence(case_id)
# 【把这个 Case 的全部 Evidence 也读出来】
# ↓
# 完整 Support Case



# Handoff
# 【问题背景】
# ↓
# Case
# 【整次调查的主档案】
# ↓
# Evidence
# 【一次次查询得到的证据】