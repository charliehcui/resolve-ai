from datetime import UTC, datetime
from uuid import uuid4

from langsmith import traceable

from backend.app.database import get_connection
from backend.app.models import UserContext
from backend.app.support_action_registry import action_policy
from backend.app.support_action_store import get_action_details, get_action_plan, save_action_step


def authorize_action_decision(user: UserContext, action: dict[str, object]) -> None:
    """按注册表检查审批身份；调用入口先通过 get_action_plan 核对公司与会话归属。"""
    if user.role not in {"staff", "admin"}:
        raise PermissionError("A scoped merchant user must confirm this action")
    policy = action_policy(action)
    if policy["approval_requirement"] == "user_action":
        raise ValueError("This action must be completed by the user outside the Agent")
    if policy["risk_level"] not in {"low", "medium", "high"} or policy["approval_requirement"] not in {"user_confirmation", "admin"}:
        raise ValueError("Unsupported action approval policy")
    if user.role != "admin" and (policy["risk_level"] in {"medium", "high"} or policy["approval_requirement"] == "admin"):
        raise PermissionError("Only a company admin can approve or reject this action")


def execute_action_plan(user: UserContext, action_id: str) -> dict[str, object]:
    from backend.app import support_action_execution

    action = get_action_plan(user, action_id)
    authorize_action_decision(user, action)
    if action["status"] in {"verified_resolved", "verification_failed", "blocked", "rejected", "expired"}:
        return get_action_details(user, action_id)
    if action["status"] not in {"approved", "executing", "awaiting_verification"}:
        raise ValueError("Action is not approved")
    executor = action_policy(action)["executor"]
    if executor is None:
        raise ValueError("This action has no backend executor")
    return getattr(support_action_execution, executor)(user, action_id)


@traceable(name="decide_action_plan", run_type="chain")
def decide_action_plan(user: UserContext, action_id: str, decision: str) -> dict[str, object]:
    if decision not in {"approve", "reject"}:
        raise ValueError("Decision must be approve or reject")
    get_action_plan(user, action_id)
    changed = False
    # 同一个计划的批准与拒绝串行处理，重复请求不覆盖原审批人或新增审批记录。
    with get_connection() as connection:
        action = connection.execute("SELECT action_type, status, expires_at, enable_order_sync, enable_shipment_sync FROM support.action_proposals WHERE action_id = %s AND company_id = %s FOR UPDATE", (action_id, user.company_id)).fetchone()
        if action is None:
            raise PermissionError("Action is not available in this company scope")
        authorize_action_decision(user, dict(action))
        stored_decision = action["status"]
        if stored_decision in {"proposed", "approved"} and action["expires_at"] <= datetime.now(UTC):
            stored_decision = "expired"
            changed = True
        elif stored_decision == "proposed":
            stored_decision = "approved" if decision == "approve" else "rejected"
            connection.execute("INSERT INTO support.action_decisions (decision_id, action_id, decision, decided_by) VALUES (%s, %s, %s, %s)", (str(uuid4()), action_id, stored_decision, user.user_id))
            changed = True
        if changed:
            connection.execute("UPDATE support.action_proposals SET status = %s, updated_at = NOW() WHERE action_id = %s", (stored_decision, action_id))
    if changed:
        policy = action_policy(dict(action))
        details = {"decision": decision, "risk_level": policy["risk_level"], "approval_requirement": policy["approval_requirement"]}
        if stored_decision == "expired":
            details.update(requested_by=user.user_id, expires_at=action["expires_at"])
        else:
            details["decided_by"] = user.user_id
        save_action_step(action_id, "human_approval", stored_decision, details)
    if decision == "approve" and stored_decision in {"approved", "executing", "awaiting_verification"}:
        return execute_action_plan(user, action_id)
    return get_action_details(user, action_id)


def explicit_confirmation(text: str) -> str | None:
    normalized = text.strip().casefold().rstrip(".!。！").strip()
    if normalized in {"yes", "confirm", "approve", "yes, execute it", "是", "确认", "确认执行", "同意执行"}:
        return "approve"
    if normalized in {"no", "reject", "cancel", "否", "拒绝", "取消"}:
        return "reject"
    return None


def respond_to_action_plan(user: UserContext, conversation_id: str, text: str, history: list[dict[str, object]]) -> dict[str, object] | None:
    # 只接受最近一条助手回复中展示的计划，不从旧消息猜测批准对象。
    if not history or history[-1].get("role") != "assistant":
        return None
    metadata = history[-1].get("metadata") or {}
    action_id = metadata.get("action_plan_id")
    if not action_id:
        return None
    action = get_action_plan(user, str(action_id))
    with get_connection() as connection:
        scoped = connection.execute("SELECT 1 FROM support.cases WHERE case_id = %s AND conversation_id = %s", (action["case_id"], conversation_id)).fetchone()
    if scoped is None:
        raise PermissionError("Action Plan does not belong to this conversation")
    decision = explicit_confirmation(text)
    if decision is None:
        if action["status"] == "proposed" and action["expires_at"] > datetime.now(UTC):
            return action_turn(get_action_details(user, str(action_id)))
        return None
    policy = action_policy(action)
    if user.role == "staff" and (policy["risk_level"] in {"medium", "high"} or policy["approval_requirement"] == "admin"):
        return action_turn(get_action_details(user, str(action_id)))
    return action_turn(decide_action_plan(user, str(action_id), decision))


def action_turn(details: dict[str, object]) -> dict[str, object]:
    messages = {
        "proposed": "这项修复计划正在等待明确确认。请回复“确认执行”或“取消”。",
        "approved": "这项修复计划已获批准，等待执行。",
        "verified_resolved": "已重新读取后台事实，确认修复完成。",
        "awaiting_verification": "修复请求已受理，后台处理与验证仍在进行。",
        "executing": "执行结果尚未确认，保留当前请求等待回执核对。",
        "rejected": "已取消这项修复计划。",
        "expired": "这项修复计划已过期，需要重新调查。",
        "blocked": "后台事实已变化或不满足安全条件，需要重新调查。",
        "verification_failed": "重新读取的后台事实未通过验证，需要继续调查或人工处理。",
    }
    policy = action_policy(details)
    answer = messages.get(str(details["status"]), "这项修复计划需要继续检查当前状态。")
    if details["status"] == "proposed" and (policy["risk_level"] in {"medium", "high"} or policy["approval_requirement"] == "admin"):
        answer = "这项修复计划需要公司管理员批准，请联系管理员处理。"
    return {"answer": answer, "status": details["status"], "case_id": details["case_id"], "action_plan_id": details["action_id"], "action_plan": details, "evidence_ids": details["evidence_ids"], "usage": {}}
