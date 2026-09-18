import time

from langsmith import Client
from langsmith.run_helpers import get_current_run_tree
from langsmith.utils import LangSmithError


def current_trace_id() -> str | None:
    run_tree = get_current_run_tree()
    if run_tree is None:
        return None
    trace_id = getattr(run_tree, "trace_id", None) or getattr(run_tree, "id", None)
    return str(trace_id) if trace_id else None


def wait_for_langsmith_run(run_id: str | None, attempts: int = 5) -> bool:
    if run_id is None:
        return False
    client = Client()
    for _ in range(attempts):
        try:
            client.read_run(run_id)
            return True
        except (LangSmithError, OSError):
            time.sleep(1)
    return False
