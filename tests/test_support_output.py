"""Output-only regressions with mock responses; no decision or prompt replacements in evaluation."""
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from backend.app.handoff import SupportHandoffRecord
from backend.app.llm import StructuredOutputError, parse_structured_output
from backend.app.support_diagnosis import SupportNextStep, decide_support_next_step


def terminal():
    return {"next_step": "finish", "investigation_complete": {"summary": "Observed failure", "confirmed_facts": [{"text": "Recorded fact", "evidence_ids": ["evidence-1"]}], "recommended_action": {"action_type": "retry_failed_task", "reason": "Explicit recommendation", "evidence_ids": ["evidence-1"]}, "outcome": "diagnosed"}}


@pytest.mark.parametrize("wrapper", ["{}", "```json\n{}\n```", "```\n{}\n```", "Investigation finished.\n```json\n{}\n```\nEnd.", "Here is the result:\n{}"])
def test_plain_markdown_and_prose_wrapped_json_preserve_every_business_field(wrapper):
    value = terminal()
    parsed = parse_structured_output(wrapper.format(json.dumps(value)), SupportNextStep)
    assert parsed.model_dump() == SupportNextStep.model_validate(value).model_dump()


def test_trailing_commas_are_repaired_only_outside_strings():
    value = '{"next_step":"finish", "investigation_complete":{"summary":"literal ,} and \\\" braces { }", "confirmed_facts":[],},}'
    parsed = parse_structured_output(value, SupportNextStep)
    assert parsed.investigation_complete.summary == 'literal ,} and " braces { }'


@pytest.mark.parametrize("content", ["", "   ", "The order failed; please retry.", '{"next_step":"finish"', '{"next_step":"finish"} {"next_step":"human_support"}', '{"next_step":"finish","next_step":"human_support"}', '{"next_step":"finish","investigation_complete":{"summary":"bad","confirmed_facts":NaN}}', "{'next_step':'finish'}", '[{"next_step":"finish"}]', '```json\n[{"next_step":"finish"}]\n```', '{"next_step":"finish","investigation_complete":{"summary":123,"confirmed_facts":[]}}'])
def test_unparseable_ambiguous_and_invalid_output_is_explicit_error_not_a_guessed_result(content):
    with pytest.raises(StructuredOutputError):
        parse_structured_output(content, SupportNextStep)


def test_support_parsing_uses_one_existing_call_without_retry_or_prompt_change(monkeypatch):
    calls = []

    def fake_model(messages, **kwargs):
        calls.append(messages)
        return AIMessage(content="Explanation\n```json\n" + json.dumps(terminal()) + "\n```", usage_metadata={"input_tokens": 5, "output_tokens": 7, "total_tokens": 12})

    monkeypatch.setattr("backend.app.support_diagnosis.call_support_model", fake_model)
    handoff = SupportHandoffRecord(handoff_id="h", conversation_id="c", company_id="company-a", customer_problem="Order failed")
    step, usage = decide_support_next_step("Investigate", handoff, [], 6)
    assert step.investigation_complete.recommended_action.action_type == "retry_failed_task"
    assert usage["total_tokens"] == 12
    assert len(calls) == 1
    assert "Choose exactly one next step." in calls[0][1].content


def test_pure_prose_fails_after_one_call_and_missing_terminal_payload_is_rejected(monkeypatch):
    calls = []

    def fake_model(messages, **kwargs):
        calls.append(messages)
        return SimpleNamespace(content="A plain explanation with no JSON", tool_calls=[], usage_metadata={})

    monkeypatch.setattr("backend.app.support_diagnosis.call_support_model", fake_model)
    handoff = SupportHandoffRecord(handoff_id="h", conversation_id="c", company_id="company-a", customer_problem="Order failed")
    with pytest.raises(StructuredOutputError):
        decide_support_next_step("Investigate", handoff, [], 6)
    assert len(calls) == 1
    monkeypatch.setattr("backend.app.support_diagnosis.call_support_model", lambda messages, **kwargs: AIMessage(content='{"next_step":"finish"}'))
    with pytest.raises(StructuredOutputError, match="payload"):
        decide_support_next_step("Investigate", handoff, [], 6)


def test_bound_tool_calls_keep_existing_selection_and_arguments(monkeypatch):
    response = AIMessage(content="ordinary prose accompanies tools", tool_calls=[{"name": "GetOrder", "args": {"shop_id": "shop-a", "order_id": "O-1"}, "id": "tool-1"}])
    monkeypatch.setattr("backend.app.support_diagnosis.call_support_model", lambda messages, **kwargs: response)
    handoff = SupportHandoffRecord(handoff_id="h", conversation_id="c", company_id="company-a", customer_problem="Order failed")
    step, _ = decide_support_next_step("Investigate", handoff, [], 6)
    assert step.next_step == "use_tool"
    assert step.tool_calls == [{"name": "GetOrder", "args": {"shop_id": "shop-a", "order_id": "O-1"}, "id": "tool-1"}]
