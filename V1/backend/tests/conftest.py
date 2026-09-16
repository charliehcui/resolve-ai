from contextlib import contextmanager

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from app import checkpointing


@pytest.fixture(autouse=True)
def use_test_checkpointer(monkeypatch: pytest.MonkeyPatch):
    serializer = JsonPlusSerializer(allowed_msgpack_modules=checkpointing.ALLOWED_CHECKPOINT_TYPES)
    checkpointer = InMemorySaver(serde=serializer)

    @contextmanager
    def open_test_checkpointer():
        yield checkpointer

    monkeypatch.setattr(checkpointing, "open_postgres_checkpointer", open_test_checkpointer)
    monkeypatch.setattr(checkpointing, "setup_postgres_checkpointer", lambda: None)
    return checkpointer
