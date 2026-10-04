"""Shared OpenRouter client with one bounded recovery attempt per logical call."""
import json
import logging
import os
import time
from threading import Lock
from uuid import uuid4

import httpx
from langchain_core.exceptions import OutputParserException
from langchain_core.runnables import RunnableLambda
from langchain_openai import ChatOpenAI
from openai import APIStatusError
from pydantic import ValidationError

from backend.app.config import get_settings
from backend.app.llm_context import call_context

logger = logging.getLogger(__name__)
parse_failures = {}
parse_lock = Lock()


class StructuredOutputError(ValueError):
    """The response cannot be interpreted without inventing structured values."""


def parse_structured_output(content, schema):
    if not isinstance(content, str) or not content.strip():
        raise StructuredOutputError("Missing structured response content")
    objects = []
    start = None
    depth = 0
    quoted = False
    escaped = False
    for index, char in enumerate(content):
        if start is None:
            if char == "{":
                start = index
                depth = 1
            continue
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                objects.append(content[start:index + 1])
                start = None
    if start is not None or len(objects) != 1:
        raise StructuredOutputError("Expected exactly one complete JSON object; prose alone or ambiguous objects cannot define a result")
    object_start = content.find(objects[0])
    if content[:object_start].rstrip().endswith("[") or content[object_start + len(objects[0]):].lstrip().startswith("]"):
        raise StructuredOutputError("Expected a JSON object, not an array")
    # 只修复字符串以外的尾逗号，不补业务字段、不截断字符串、不替换引号。
    value = objects[0]
    repaired = []
    quoted = False
    escaped = False
    for index, char in enumerate(value):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == "," and value[index + 1:].lstrip().startswith(("}", "]")):
            continue
        repaired.append(char)

    def unique_keys(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise StructuredOutputError("Duplicate JSON key: " + key)
            result[key] = item
        return result

    def reject_constant(value):
        raise StructuredOutputError("Non-JSON constant: " + value)

    try:
        data = json.loads("".join(repaired), object_pairs_hook=unique_keys, parse_constant=reject_constant)
        return schema.model_validate(data, strict=True)
    except (json.JSONDecodeError, ValidationError) as error:
        raise StructuredOutputError("Invalid structured response: " + str(error)) from error


def repeated_parse_failure(schema_key) -> bool:
    with parse_lock:
        parse_failures[schema_key] = parse_failures.get(schema_key, 0) + 1
        return parse_failures[schema_key] >= 2


def failure_reason(error: Exception) -> str | None:
    if not isinstance(error, APIStatusError):
        return None
    if error.status_code == 429:
        return "rate_limit"
    if 500 <= error.status_code <= 599:
        return "provider_5xx"
    if error.status_code == 404 and "endpoint" in str(error).lower():
        return "provider_unavailable"
    return None


class OpenRouterModel:
    def __init__(self, temperature: float, scope: str, reasoning_effort: str | None = None):
        self.temperature = temperature
        self.scope = scope
        self.reasoning_effort = reasoning_effort
        self.callbacks = []
        self.tags = []

    def client(self, model: str, provider: str):
        settings = get_settings()
        extra = {"provider": {"only": [provider], "allow_fallbacks": False, "require_parameters": True}, "usage": {"include": True}}
        if model == settings.openrouter_fallback_model:
            extra["reasoning"] = {"effort": "low"}
        elif self.scope == "judge" or self.reasoning_effort is not None:
            extra["reasoning"] = {"effort": self.reasoning_effort or "none"}
        options = {}
        if os.getenv("EVAL_RUN_ID"):
            from evals.budget import accounting_hooks, rates

            price = rates(model, provider)
            extra["provider"]["max_price"] = {"prompt": price["prompt"] * 1_000_000, "completion": price["completion"] * 1_000_000}
            options["http_client"] = httpx.Client(event_hooks=accounting_hooks(self.scope), timeout=60)
        output_limit = int(os.getenv("OPENROUTER_MAX_OUTPUT_TOKENS", "4096"))
        if self.reasoning_effort == "low":
            output_limit = max(output_limit, 6144)
        return ChatOpenAI(model=model, api_key=settings.openrouter_api_key, base_url="https://openrouter.ai/api/v1", temperature=self.temperature, timeout=60, max_retries=0, max_tokens=output_limit, use_responses_api=False, extra_body=extra, callbacks=self.callbacks, tags=self.tags, **options)

    def recovery_endpoint(self, reason: str | None = None):
        settings = get_settings()
        if os.getenv("EVAL_RUN_ID"):
            from evals.budget import rates

            snapshot = rates()
            endpoint = snapshot.get("fallback_endpoint") if reason == "rate_limit" else None
            endpoint = endpoint or snapshot.get("retry_endpoint") or snapshot.get("fallback_endpoint")
            return (endpoint["model"], endpoint["routing_tag"]) if endpoint else None
        if reason == "rate_limit" and settings.openrouter_fallback_model and settings.openrouter_fallback_provider:
            return settings.openrouter_fallback_model, settings.openrouter_fallback_provider
        if settings.openrouter_retry_provider:
            return settings.openrouter_model, settings.openrouter_retry_provider
        if settings.openrouter_fallback_model and settings.openrouter_fallback_provider:
            return settings.openrouter_fallback_model, settings.openrouter_fallback_provider
        return None

    def call(self, value, config, operation, schema_key=None, **kwargs):
        settings = get_settings()
        metadata = {"logical_call_id": uuid4().hex, "primary_model": settings.openrouter_model, "fallback_model": settings.openrouter_fallback_model or None, "fallback_reason": None, "fallback_count": 0, "scope": self.scope}
        token = call_context.set(metadata)
        started = time.perf_counter()
        response = None
        try:
            reason = None
            try:
                response = operation(self.client(settings.openrouter_model, settings.openrouter_provider)).invoke(value, config=config, **kwargs)
            except APIStatusError as error:
                reason = failure_reason(error)
                if reason is None or self.recovery_endpoint(reason) is None:
                    raise
            except (OutputParserException, ValidationError, json.JSONDecodeError):
                if not schema_key or not repeated_parse_failure(schema_key) or self.recovery_endpoint() is None:
                    raise
                reason = "consecutive_structured_parse_failures"
            if schema_key and response is not None:
                if response.get("parsing_error") is not None:
                    if repeated_parse_failure(schema_key):
                        reason = "consecutive_structured_parse_failures"
                else:
                    with parse_lock:
                        parse_failures[schema_key] = 0
            endpoint = self.recovery_endpoint(reason) if reason else None
            if endpoint:
                metadata.update({"fallback_count": 1, "fallback_reason": reason, "recovery_model": endpoint[0], "recovery_provider": endpoint[1]})
                # This attempt is outside the primary exception handler: its failure terminates the call.
                response = operation(self.client(*endpoint)).invoke(value, config=config, **kwargs)
            raw = response.get("raw") if isinstance(response, dict) else response
            if raw is not None and hasattr(raw, "response_metadata"):
                raw.response_metadata["fallback"] = dict(metadata)
            return response
        finally:
            if os.getenv("EVAL_RUN_ID"):
                from evals.budget import calls_for_run

                rows = [row for row in calls_for_run(os.environ["EVAL_RUN_ID"]) if row["logical_call_id"] == metadata["logical_call_id"]]
                metadata["input_tokens"] = sum(row["input_tokens"] or 0 for row in rows)
                metadata["output_tokens"] = sum(row["output_tokens"] or 0 for row in rows)
                metadata["extra_cost_usd"] = sum(row["charged_cost"] for row in rows if row["fallback_count"])
                metadata["extra_actual_cost_usd"] = sum(row["actual_cost"] or 0 for row in rows if row["fallback_count"])
            else:
                raw = response.get("raw") if isinstance(response, dict) else response
                metadata["token_usage"] = getattr(raw, "usage_metadata", None)
                usage = getattr(raw, "response_metadata", {}).get("token_usage", {})
                metadata["extra_cost_usd"] = usage.get("cost") if metadata["fallback_count"] else 0
            metadata["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
            logger.info("openrouter_call %s", json.dumps(metadata))
            raw = response.get("raw") if isinstance(response, dict) else response
            if raw is not None and hasattr(raw, "response_metadata"):
                raw.response_metadata["fallback"] = dict(metadata)
            call_context.reset(token)

    def invoke(self, value, config=None, **kwargs):
        return self.call(value, config, lambda model: model, **kwargs)

    def bind_tools(self, tools, **kwargs):
        return RunnableLambda(lambda value, config: self.call(value, config, lambda model: model.bind_tools(tools, **kwargs)))

    def with_structured_output(self, schema=None, *, include_raw=False, **kwargs):
        key = (get_settings().openrouter_model, str(schema))

        def invoke(value, config):
            result = self.call(value, config, lambda model: model.with_structured_output(schema, include_raw=True, **kwargs), schema_key=key)
            if include_raw:
                return result
            if result["parsing_error"] is not None:
                raise result["parsing_error"]
            return result["parsed"]

        return RunnableLambda(invoke)


def create_model(temperature: float = 0, max_retries: int = 0, scope: str = "application", reasoning_effort: str | None = None) -> OpenRouterModel:
    return OpenRouterModel(temperature, scope, reasoning_effort)
