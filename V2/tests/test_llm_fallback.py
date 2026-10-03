"""Synthetic client tests. These are harness checks, not model-quality metrics."""
import httpx
import pytest
from langchain_core.messages import AIMessage
from openai import APIStatusError

from backend.app.config import get_settings
from backend.app.llm import OpenRouterModel, create_model, parse_failures
from evals.budget import BudgetExceeded


def test_judge_reasoning_control_does_not_change_application_calls(monkeypatch):
    monkeypatch.delenv("EVAL_RUN_ID", raising=False)
    parameters = []
    monkeypatch.setattr("backend.app.llm.ChatOpenAI", lambda **kwargs: parameters.append(kwargs))
    create_model(scope="judge").client("test-model", "test-provider")
    create_model().client("test-model", "test-provider")
    assert parameters[0]["extra_body"]["reasoning"] == {"effort": "none"}
    assert "reasoning" not in parameters[1]["extra_body"]
    assert parameters[0]["extra_body"]["provider"] == parameters[1]["extra_body"]["provider"]
    assert parameters[0]["max_tokens"] == parameters[1]["max_tokens"] == 4096


def test_configured_backup_uses_supported_low_reasoning_for_judge_and_application(monkeypatch):
    monkeypatch.delenv("EVAL_RUN_ID", raising=False)
    parameters = []
    monkeypatch.setattr("backend.app.llm.ChatOpenAI", lambda **kwargs: parameters.append(kwargs))
    settings = get_settings()
    for scope in ("judge", "application"):
        create_model(scope=scope).client(settings.openrouter_fallback_model, settings.openrouter_fallback_provider)
    assert all(value["extra_body"]["reasoning"] == {"effort": "low"} for value in parameters)


def status_error(status):
    response = httpx.Response(status, request=httpx.Request("POST", "https://example.test/chat"))
    return APIStatusError("Provider endpoint unavailable", response=response, body={})


@pytest.fixture
def clients(monkeypatch):
    monkeypatch.delenv("EVAL_RUN_ID", raising=False)
    monkeypatch.setenv("OPENROUTER_RETRY_PROVIDER", "same-model-backup")
    monkeypatch.setenv("OPENROUTER_FALLBACK_MODEL", "synthetic-backup-model")
    monkeypatch.setenv("OPENROUTER_FALLBACK_PROVIDER", "backup-provider")
    get_settings.cache_clear()
    parse_failures.clear()
    responses = []
    attempts = []

    class Stub:
        def invoke(self, value, config=None, **kwargs):
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        def bind_tools(self, tools, **kwargs):
            return self

        def with_structured_output(self, schema, **kwargs):
            return self

    def client(self, model, provider):
        attempts.append((model, provider))
        return Stub()

    monkeypatch.setattr(OpenRouterModel, "client", client)
    yield responses, attempts
    get_settings.cache_clear()
    parse_failures.clear()


def test_rate_limit_uses_configured_backup_model_once(clients):
    responses, attempts = clients
    responses.extend([status_error(429), AIMessage(content="ok")])
    result = create_model().bind_tools([]).invoke("probe")
    assert attempts == [("test-model", "test-provider"), ("synthetic-backup-model", "backup-provider")]
    assert result.response_metadata["fallback"]["fallback_reason"] == "rate_limit"
    assert result.response_metadata["fallback"]["fallback_count"] == 1


def test_second_failure_terminates_without_third_request(clients):
    responses, attempts = clients
    responses.extend([status_error(503), status_error(429)])
    with pytest.raises(APIStatusError):
        create_model().invoke("probe")
    assert len(attempts) == 2


@pytest.mark.parametrize("status", [400, 401, 402, 403, 422])
def test_nonrecoverable_failure_does_not_fallback(clients, status):
    responses, attempts = clients
    responses.append(status_error(status))
    with pytest.raises(APIStatusError):
        create_model().invoke("probe")
    assert len(attempts) == 1


def test_low_quality_or_empty_answer_does_not_fallback(clients):
    responses, attempts = clients
    responses.append(AIMessage(content=""))
    assert create_model().invoke("probe").content == ""
    assert len(attempts) == 1


def test_backup_model_used_only_when_same_model_endpoint_is_absent(clients, monkeypatch):
    responses, attempts = clients
    monkeypatch.setenv("OPENROUTER_RETRY_PROVIDER", " ")
    get_settings.cache_clear()
    responses.extend([status_error(503), AIMessage(content="ok")])
    create_model().invoke("probe")
    assert attempts[-1] == ("synthetic-backup-model", "backup-provider")


def test_two_consecutive_parse_failures_trigger_one_recovery(clients):
    responses, attempts = clients
    bad = {"raw": AIMessage(content="invalid JSON"), "parsed": None, "parsing_error": ValueError("parse failed")}
    good = {"raw": AIMessage(content="{}"), "parsed": {"ok": True}, "parsing_error": None}
    responses.extend([bad, bad, good])
    model = create_model().with_structured_output(dict, include_raw=True)
    assert model.invoke("probe")["parsed"] is None
    result = model.invoke("probe")
    assert result["parsed"] == {"ok": True}
    assert result["raw"].response_metadata["fallback"]["fallback_reason"] == "consecutive_structured_parse_failures"
    assert len(attempts) == 3


def test_primary_parse_success_resets_failure_streak(clients):
    responses, attempts = clients
    bad = {"raw": AIMessage(content="invalid JSON"), "parsed": None, "parsing_error": ValueError("parse failed")}
    good = {"raw": AIMessage(content="{}"), "parsed": {"ok": True}, "parsing_error": None}
    responses.extend([bad, good, bad])
    model = create_model().with_structured_output(dict, include_raw=True)
    for _ in range(3):
        model.invoke("probe")
    assert len(attempts) == 3


def test_budget_exceeded_is_never_retried(clients):
    responses, attempts = clients
    responses.append(BudgetExceeded("budget blocked"))
    with pytest.raises(BudgetExceeded):
        create_model().invoke("probe")
    assert len(attempts) == 1
