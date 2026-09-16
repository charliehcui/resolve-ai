from contextlib import contextmanager
from collections.abc import Iterator

from langgraph.checkpoint.postgres import PostgresSaver

from app.core.config import settings


def get_checkpoint_database_url() -> str:
    return settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@contextmanager
def open_postgres_checkpointer() -> Iterator[PostgresSaver]:
    with PostgresSaver.from_conn_string(get_checkpoint_database_url()) as checkpointer:
        yield checkpointer


def setup_postgres_checkpointer() -> None:
    with open_postgres_checkpointer() as checkpointer:
        checkpointer.setup()
