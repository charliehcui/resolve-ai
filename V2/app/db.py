import json
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from app.config import PROJECT_ROOT, psycopg_url, require_env

MIGRATION_FILES = sorted((PROJECT_ROOT / "db").glob("*.sql"))


@contextmanager
def get_connection() -> Iterator[psycopg.Connection]:
    database_url = psycopg_url(require_env("DATABASE_URL"))
    with psycopg.connect(database_url, row_factory=dict_row, connect_timeout=3) as connection:
        yield connection


def initialize_database() -> None:
    with get_connection() as connection:
        for migration_file in MIGRATION_FILES:
            sql = migration_file.read_text(encoding="utf-8")
            for statement in sql.split(";"):
                if statement.strip():
                    connection.execute(statement)


def database_is_ready() -> bool:
    with get_connection() as connection:
        row = connection.execute("SELECT 1 AS ready").fetchone()
    return bool(row and row["ready"] == 1)


def create_conversation(company_id: str, user_id: str) -> str:
    conversation_id = str(uuid4())
    with get_connection() as connection:
        connection.execute("INSERT INTO support.conversations (conversation_id, company_id, user_id) VALUES (%s, %s, %s)", (conversation_id, company_id, user_id))
    return conversation_id


def get_conversation(conversation_id: str) -> dict[str, object] | None:
    with get_connection() as connection:
        return connection.execute("SELECT conversation_id, company_id, user_id, active_role FROM support.conversations WHERE conversation_id = %s", (conversation_id,)).fetchone()


def save_message(conversation_id: str, role: str, content: str, metadata: dict[str, object] | None = None) -> str:
    message_id = str(uuid4())
    with get_connection() as connection:
        connection.execute("INSERT INTO support.messages (message_id, conversation_id, role, content, metadata) VALUES (%s, %s, %s, %s, %s::jsonb)", (message_id, conversation_id, role, content, json.dumps(metadata or {})))
    return message_id


def load_messages(conversation_id: str, limit: int = 8) -> list[dict[str, object]]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT role, content, metadata, created_at FROM support.messages WHERE conversation_id = %s ORDER BY created_at DESC LIMIT %s",
            (conversation_id, limit),
        ).fetchall()
    return list(reversed(rows))


def save_agent_run(conversation_id: str, provider: str, model: str, purpose: str, status: str, latency_ms: int, usage: dict[str, int | None], trace_id: str | None, error_type: str | None = None) -> str:
    run_id = str(uuid4())
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO support.agent_runs (run_id, conversation_id, provider, model, purpose, status, latency_ms, input_tokens, output_tokens, total_tokens, trace_id, error_type)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (run_id, conversation_id, provider, model, purpose, status, latency_ms, usage.get("input_tokens"), usage.get("output_tokens"), usage.get("total_tokens"), trace_id, error_type),
        )
    return run_id
