from datetime import UTC, datetime
from uuid import uuid4

from langsmith import traceable

from backend.app.database import get_connection
from backend.app.models import UserContext
from backend.app.support_action_registry import action_policy
from backend.app.support_action_store import get_action_details, get_action_plan, save_action_step


def authorize_action_decision(user: UserContext, action: dict[str, object]) -> None:
    policy = action_policy(action)
    if policy["approval_requirement"] == "user_action":
        raise ValueError("This action must be completed by the user outside the Agent")
    if policy["risk_level"] in {"medium", "high"} or policy["approval_requirement"] == "admin":
        if user.role != "admin":
            raise PermissionError("Only a company admin can approve this action")
    elif user.role not in {"staff", "admin"}:
        raise PermissionError("A scoped merchant user must confirm this action")


def execute_action_plan(user: UserContext, action_id: str) -> dict[str, object]:
    from backend.app import support_action_execution

    action = get_action_plan(user, action_id)
    authorize_action_decision(user, action)
    executor = action_policy(action)["executor"]
    if executor is None:
        raise ValueError("This action has no backend executor")
    return getattr(support_action_execution, executor)(user, action_id)


@traceable(name="decide_action_plan", run_type="chain")
def decide_action_plan(user: UserContext, action_id: str, decision: str) -> dict[str, object]:
    if decision not in {"approve", "reject"}:
        raise ValueError("Decision must be approve or reject")
    action = get_action_plan(user, action_id)
    authorize_action_decision(user, action)
    # Serialize approval/rejection for the same specific plan.
    with get_connection() as connection:
        action = connection.execute("SELECT status, expires_at FROM support.action_proposals WHERE action_id = %s FOR UPDATE", (action_id,)).fetchone()
        if action["status"] == "proposed":
            if action["expires_at"] <= datetime.now(UTC):
                stored_decision = "expired"
            else:
                stored_decision = "approved" if decision == "approve" else "rejected"
                connection.execute("INSERT INTO support.action_decisions (decision_id, action_id, decision, decided_by) VALUES (%s, %s, %s, %s)", (str(uuid4()), action_id, stored_decision, user.user_id))
            connection.execute("UPDATE support.action_proposals SET status = %s, updated_at = NOW() WHERE action_id = %s", (stored_decision, action_id))
        else:
            stored_decision = action["status"]
    save_action_step(action_id, "human_approval", stored_decision, {"decided_by": user.user_id, "decision": decision})
    if decision == "approve" and stored_decision in {"approved", "executing", "awaiting_verification"}:
        return execute_action_plan(user, action_id)
    return get_action_details(user, action_id)


def explicit_confirmation(text: str) -> str | None:
    normalized = text.strip().lower().removesuffix(".").removesuffix("!").removesuffix("。").removesuffix("！")
    if normalized in {"yes", "confirm", "approve", "yes, execute it", "是", "确认", "确认执行", "同意执行"}:
        return "approve"
    if normalized in {"no", "reject", "cancel", "否", "拒绝", "取消"}:
        return "reject"
    return None


def respond_to_action_plan(user: UserContext, conversation_id: str, text: str, history: list[dict[str, object]]) -> dict[str, object] | None:
    # Chat confirmation binds to the plan displayed in the latest assistant message.
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
    details = decide_action_plan(user, str(action_id), decision)
    return action_turn(details)


def action_turn(details: dict[str, object]) -> dict[str, object]:
    messages = {
        "verified_resolved": "已重新读取后台事实，确认修复完成。",
        "awaiting_verification": "修复请求已受理，后台处理与验证仍在进行。",
        "executing": "执行结果尚未确认，保留当前请求等待回执核对。",
        "rejected": "已取消这项修复计划。",
        "expired": "这项修复计划已过期，需要重新调查。",
        "blocked": "后台事实已变化或不满足安全条件，需要重新调查。",
        "verification_failed": "重新读取的后台事实未通过验证，需要继续调查或人工处理。",
    }
    return {"answer": messages.get(str(details["status"]), "这项修复计划正在等待明确确认。"), "status": details["status"], "case_id": details["case_id"], "action_plan_id": details["action_id"], "action_plan": details, "evidence_ids": details["evidence_ids"], "usage": {}}
