"""Evaluation-only observation: wrappers always call the unchanged application function."""
import json
import time
from contextlib import ExitStack
from threading import Lock
from unittest.mock import patch

from langchain_core.callbacks import BaseCallbackHandler


def interval_ms(intervals: list[tuple[float, float]]) -> float:
    total = 0.0
    end = 0.0
    for start, stop in sorted(intervals):
        total += max(0.0, stop - max(start, end))
        end = max(end, stop)
    return round(total * 1000, 3)


class Observer(BaseCallbackHandler):
    def __init__(self, journal_path=None):
        self.lock = Lock()
        self.journal_lock = Lock()
        self.journal_path = journal_path
        self.journal_errors = []
        self.calls = {}
        self.retrieval = []
        self.tools = []
        self.before_claims = []
        self.after_claims = []
        self.diagnoses = []
        self.stack = ExitStack()

    def save(self):
        if self.journal_path is not None:
            with self.journal_lock:
                temporary = self.journal_path.with_suffix(".tmp")
                try:
                    temporary.write_text(json.dumps(self.snapshot(), ensure_ascii=False, default=str), encoding="utf-8")
                    for attempt in range(3):
                        try:
                            temporary.replace(self.journal_path)
                            break
                        except OSError:
                            if attempt == 2:
                                raise
                            time.sleep(0.01)
                except OSError as error:
                    self.journal_errors.append({"error_type": type(error).__name__, "reason": "Partial observation journal could not be persisted; completed-case observations remain in memory."})

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        with self.lock:
            self.calls[str(run_id)] = {"start": time.perf_counter(), "model": kwargs.get("invocation_params", {}).get("model"), "scope": "support" if "eval_support" in kwargs.get("tags", []) else "customer", "status": "running", "input_tokens": None, "output_tokens": None}
        self.save()

    def on_llm_end(self, response, *, run_id, **kwargs):
        from backend.app.customer_agent import get_token_usage

        message = response.generations[0][0].message
        usage = get_token_usage(message)
        with self.lock:
            self.calls[str(run_id)].update({"end": time.perf_counter(), "status": "completed", "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"), "tool_calls": getattr(message, "tool_calls", []), "content": message.content})
        self.save()

    def on_llm_error(self, error, *, run_id, **kwargs):
        with self.lock:
            self.calls[str(run_id)].update({"end": time.perf_counter(), "status": "error", "error_type": type(error).__name__})
        self.save()

    def __enter__(self):
        from backend.app import citations, customer_agent, customer_workflow, support_action_execution, support_diagnosis, support_tools, support_workflow

        for module, name in ((customer_agent, "create_model"), (citations, "create_model"), (support_diagnosis, "create_model")):
            original = getattr(module, name)

            scope = "eval_support" if module is support_diagnosis else "eval_customer"

            def observed_model(*args, _original=original, _scope=scope, **kwargs):
                model = _original(*args, **kwargs)
                model.callbacks = [self]
                model.tags = [*(model.tags or []), _scope]
                return model

            self.stack.enter_context(patch.object(module, name, observed_model))

        original_validation = customer_agent.validate_claims

        def observe_validation(claims, *args, **kwargs):
            self.before_claims = [claim.model_dump() for claim in claims]
            self.save()
            result = original_validation(claims, *args, **kwargs)
            self.after_claims = [claim.model_dump() for claim in result[0]]
            self.save()
            return result

        self.stack.enter_context(patch.object(customer_agent, "validate_claims", observe_validation))
        original_retrieval = customer_workflow.retrieve_customer_documents

        def observe_retrieval(*args, **kwargs):
            record = {"start": time.perf_counter(), "status": "running"}
            self.retrieval.append(record)
            self.save()
            try:
                result = original_retrieval(*args, **kwargs)
                record.update({"status": "completed", "chunks": [chunk.model_dump() for chunk in result]})
                return result
            finally:
                record["end"] = time.perf_counter()
                self.save()

        self.stack.enter_context(patch.object(customer_workflow, "retrieve_customer_documents", observe_retrieval))
        original_read = support_tools.call_read_service

        def observe_read(*args, **kwargs):
            record = {"start": time.perf_counter(), "name": args[0] if args else kwargs["tool_name"]}
            with self.lock:
                self.tools.append(record)
            self.save()
            try:
                result = original_read(*args, **kwargs)
                record.update({"status": result.status, "result": result.model_dump()})
                return result
            finally:
                record["end"] = time.perf_counter()
                self.save()

        for module in (support_tools, support_action_execution):
            self.stack.enter_context(patch.object(module, "call_read_service", observe_read))
        for name in ("submit_order_repair", "submit_shipment_repair", "submit_background_repair"):
            original = getattr(support_action_execution, name)

            def observe_write(*args, _original=original, _name=name, **kwargs):
                record = {"start": time.perf_counter(), "name": _name, "kind": "write", "status": "running"}
                self.tools.append(record)
                self.save()
                try:
                    result = _original(*args, **kwargs)
                    record.update({"status": "completed", "result": result})
                    return result
                finally:
                    record["end"] = time.perf_counter()
                    self.save()

            self.stack.enter_context(patch.object(support_action_execution, name, observe_write))
        original_step = support_workflow.decide_support_next_step

        def observe_step(*args, **kwargs):
            result = original_step(*args, **kwargs)
            self.diagnoses.append(result[0].model_dump())
            self.save()
            return result

        self.stack.enter_context(patch.object(support_workflow, "decide_support_next_step", observe_step))
        return self

    def __exit__(self, *args):
        self.stack.close()

    def snapshot(self) -> dict:
        with self.lock:
            calls = [dict(call) for call in self.calls.values()]
        selected = [tool for call in calls if call["scope"] == "support" for tool in call.get("tool_calls", [])]
        usage_complete = all(call["input_tokens"] is not None and call["output_tokens"] is not None for call in calls)
        return {"llm_calls": calls, "selected_tools": selected, "tool_requests": self.tools, "retrieval": self.retrieval, "before_claims": self.before_claims, "after_claims": self.after_claims, "diagnoses": self.diagnoses, "journal_errors": list(self.journal_errors), "performance": {"llm_latency_ms": interval_ms([(call["start"], call["end"]) for call in calls if "end" in call]), "retrieval_latency_ms": interval_ms([(item["start"], item["end"]) for item in self.retrieval if "end" in item]), "tool_execution_latency_ms": interval_ms([(item["start"], item["end"]) for item in self.tools if "end" in item]), "llm_call_count": len(calls), "tool_call_count": len(self.tools), "selected_tool_call_count": len(selected), "input_tokens": sum(call["input_tokens"] for call in calls) if usage_complete else None, "output_tokens": sum(call["output_tokens"] for call in calls) if usage_complete else None, "token_usage_complete": usage_complete}}
