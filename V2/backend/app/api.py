from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from backend.app import support_action_plans
from backend.app.auth import authenticate, authorize_conversation
from backend.app.conversations import process_conversation_message
from backend.app.database import create_conversation, get_connection, load_messages
from backend.app.models import UserContext
from backend.app.report import render_ticket_html
from backend.app.support_action_approvals import decide_action_plan, execute_action_plan, get_action_details
from backend.app.support_action_registry import ACTION_ALIASES, action_policy
from backend.app.support_cases import show_case
from backend.app.tickets import create_ticket, list_engineer_tickets, recheck_ticket, show_ticket


class MessageRequest(BaseModel):
    question: str
    retrieval_mode: Literal["vector_only", "hybrid", "hybrid_rerank"] | None = None


class TicketRequest(BaseModel):
    conversation_id: str
    reason: str = "User requested engineer support"


class ActionPlanRequest(BaseModel):
    action_type: str
    enable_order_sync: bool = False
    enable_shipment_sync: bool = False


class DecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]


app = FastAPI(title="ResolveAI V2 API", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(PermissionError)
async def permission_error(_: Request, error: PermissionError) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(error)})


@app.exception_handler(ValueError)
async def value_error(_: Request, error: ValueError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(error)})


def current_auth(authorization: str = Header(default="")) -> UserContext:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise PermissionError("Bearer access token is required")
    return authenticate(token)


AuthDependency = Annotated[UserContext, Depends(current_auth)]


def conversation_snapshot(user: UserContext, conversation_id: str) -> dict[str, object]:
    conversation = authorize_conversation(user, conversation_id)
    with get_connection() as connection:
        case_row = connection.execute("SELECT case_id::text FROM support.cases WHERE conversation_id = %s", (conversation_id,)).fetchone()
        action_rows = connection.execute("SELECT p.action_id::text FROM support.action_proposals p JOIN support.cases c ON c.case_id = p.case_id WHERE c.conversation_id = %s ORDER BY p.created_at", (conversation_id,)).fetchall()
        ticket_row = connection.execute("SELECT ticket_id::text FROM support.tickets WHERE conversation_id = %s ORDER BY created_at DESC LIMIT 1", (conversation_id,)).fetchone()
    return {
        "conversation": {**conversation, "conversation_id": str(conversation["conversation_id"])},
        "messages": load_messages(conversation_id, limit=100),
        "case": show_case(case_row["case_id"], user) if case_row else None,
        "actions": [get_action_details(user, row["action_id"]) for row in action_rows],
        "ticket": show_ticket(user, ticket_row["ticket_id"]) if ticket_row else None,
    }


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok", "api_version": "v1", "product_version": "2.0"}


@app.post("/api/v1/conversations")
def start_conversation(user: AuthDependency) -> dict[str, object]:
    if user.role == "engineer":
        raise PermissionError("Engineer identities cannot create merchant conversations")
    return conversation_snapshot(user, create_conversation(user.company_id, user.user_id))


@app.get("/api/v1/conversations/{conversation_id}")
def get_conversation_snapshot(conversation_id: str, user: AuthDependency) -> dict[str, object]:
    return conversation_snapshot(user, conversation_id)


@app.post("/api/v1/conversations/{conversation_id}/messages")
def send_message(conversation_id: str, request: MessageRequest, user: AuthDependency) -> dict[str, object]:
    turn = process_conversation_message(user, request.question, conversation_id, request.retrieval_mode)
    return {"turn": turn, "snapshot": conversation_snapshot(user, conversation_id)}


@app.post("/api/v1/cases/{case_id}/actions")
def create_action_plan(case_id: str, request: ActionPlanRequest, user: AuthDependency) -> dict[str, object]:
    action_type = ACTION_ALIASES.get(request.action_type, request.action_type)
    policy = action_policy({"action_type": action_type})
    builder = getattr(support_action_plans, policy["builder"])
    if action_type == "resend_shipment":
        return builder(user, case_id, request.enable_shipment_sync)
    if action_type in {"retry_order_sync", "retry_failed_task"}:
        return builder(user, case_id, request.enable_order_sync, action_type)
    return builder(user, case_id)


@app.get("/api/v1/actions/{action_id}")
def get_action(action_id: str, user: AuthDependency) -> dict[str, object]:
    return get_action_details(user, action_id)


@app.post("/api/v1/actions/{action_id}/decision")
def action_decision(action_id: str, request: DecisionRequest, user: AuthDependency) -> dict[str, object]:
    return decide_action_plan(user, action_id, request.decision)


@app.post("/api/v1/actions/{action_id}/execute")
def action_execution(action_id: str, user: AuthDependency) -> dict[str, object]:
    return execute_action_plan(user, action_id)


@app.post("/api/v1/tickets")
def request_engineer(request: TicketRequest, user: AuthDependency) -> dict[str, object]:
    return create_ticket(user, request.conversation_id, "user_requested", request.reason)


@app.get("/api/v1/tickets/{ticket_id}")
def get_ticket(ticket_id: str, user: AuthDependency) -> dict[str, object]:
    return show_ticket(user, ticket_id)


@app.post("/api/v1/tickets/{ticket_id}/recheck")
def ticket_recheck(ticket_id: str, user: AuthDependency) -> dict[str, object]:
    return recheck_ticket(user, ticket_id)


@app.get("/api/v1/tickets/{ticket_id}/export", response_class=HTMLResponse)
def ticket_export(ticket_id: str, user: AuthDependency) -> str:
    return render_ticket_html(show_ticket(user, ticket_id))


@app.get("/api/v1/engineer/tickets")
def engineer_tickets(user: AuthDependency) -> list[dict[str, object]]:
    return list_engineer_tickets(user)


#api.py 是整个后端的 HTTP 入口。前端或其他客户端通过 URL 调用这里，然后这里再去调用我们前面学过的 Python 业务函数

# Frontend / Client
# ↓
# HTTP Request
# ↓
# FastAPI Route
# ↓
# AuthDependency
# 【验证 Bearer Token】
# ↓
# Pydantic Request Model
# 【检查 Body 数据】
# ↓
# 调用项目里的 Python Function
# ↓
# Customer / Support / Action / Ticket / Database
# ↓
# 返回 dict
# ↓
# FastAPI 自动转成 JSON
# ↓
# Frontend


