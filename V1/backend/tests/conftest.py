from contextlib import contextmanager

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app import checkpointing


@pytest.fixture(autouse=True)
def use_test_checkpointer(monkeypatch: pytest.MonkeyPatch):
    checkpointer = InMemorySaver()

    @contextmanager
    def open_test_checkpointer():
        yield checkpointer

    monkeypatch.setattr(checkpointing, "open_postgres_checkpointer", open_test_checkpointer)
    monkeypatch.setattr(checkpointing, "setup_postgres_checkpointer", lambda: None)
    return checkpointer
