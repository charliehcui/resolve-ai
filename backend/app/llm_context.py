"""Metadata for the current logical call, shared with HTTP accounting hooks."""
from contextvars import ContextVar

call_context = ContextVar("openrouter_call_context", default=None)
