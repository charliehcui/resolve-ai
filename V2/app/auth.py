import hashlib

from app.db import get_connection, get_conversation
from app.models import AuthContext


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def authenticate(token: str) -> AuthContext:
    if not token:
        raise PermissionError("A valid access token is required")
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT u.company_id, u.user_id, u.role
            FROM support.access_tokens t
            JOIN support.users u ON u.user_id = t.user_id
            WHERE t.token_hash = %s AND t.revoked_at IS NULL
            """,
            (hash_token(token),),
        ).fetchone()
    if row is None:
        raise PermissionError("Invalid access token")
    return AuthContext(**row)


def authorize_conversation(auth: AuthContext, conversation_id: str) -> dict[str, object]:
    conversation = get_conversation(conversation_id)
    if conversation is None or conversation["company_id"] != auth.company_id or conversation["user_id"] != auth.user_id:
        raise PermissionError("Conversation is not available in this user scope")
    return conversation

