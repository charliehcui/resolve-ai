from contextlib import contextmanager
from collections.abc import Iterator

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg import Connection
from psycopg.rows import dict_row

from app.core.config import settings


ALLOWED_CHECKPOINT_TYPES = [
    ("app.actions", "ActionExecutionResponse"),
    ("app.actions", "ActionPolicyDecision"),
    ("app.actions", "ExecutableAction"),
    ("app.customer_agent", "CustomerResolution"),
    ("app.customer_agent", "CustomerVerification"),
    ("app.customer_agent", "ProblemDetails"),
    ("app.handoff", "SupportHandoff"),
    ("app.support_results", "ActionProposal"),
    ("app.support_results", "EngineerEscalationPackage"),
    ("app.support_results", "EvidenceItem"),
    ("app.support_results", "SupportDiagnosis"),
    ("app.support_results", "SupportInvestigationResult"),
    ("app.tickets", "TicketContext"),
    ("app.tickets", "TicketStatus"),
]


def get_checkpoint_database_url() -> str:
    return settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@contextmanager
def open_postgres_checkpointer() -> Iterator[PostgresSaver]:
    serializer = JsonPlusSerializer(allowed_msgpack_modules=ALLOWED_CHECKPOINT_TYPES)

    with Connection.connect(get_checkpoint_database_url(), autocommit=True, prepare_threshold=0, row_factory=dict_row) as connection:
        yield PostgresSaver(connection, serde=serializer)


def setup_postgres_checkpointer() -> None:
    with open_postgres_checkpointer() as checkpointer:
        checkpointer.setup()
