import pytest
from pydantic import ValidationError

from backend.app.config import get_settings
from backend.app.customer_agent import direct_customer_query, needs_query_understanding
from backend.app.llm import create_model
from backend.app.models import CustomerQueryDecision


def test_simple_question_and_single_knowledge_gap_skip_planning():
    assert direct_customer_query("库存规则中，可售库存如何计算？", []).search_query == "库存规则中，可售库存如何计算？"
    assert direct_customer_query("company-a 2.0 库存规则中，可售库存如何计算？", []).version == "2.0"
    assert direct_customer_query("当前产品资料是否说明自动计算并申报澳大利亚跨境 GST？没有说明就明确说不知道。", []) is not None


def test_compound_conditional_and_followup_questions_keep_understanding():
    assert needs_query_understanding("订单恢复和发货恢复分别需要哪些事实与审批？怎样确认恢复真正完成？", [])
    assert direct_customer_query("如果版本相同但数量错误，是否允许刷新？", []) is None
    assert direct_customer_query("这是什么意思？", [{"role": "user", "content": "warehouse version=5, source_version=6"}]) is None


@pytest.mark.parametrize("effort", ["none", "low"])
def test_explicit_stage_limit_does_not_expand_the_reasoning_budget(monkeypatch, effort):
    monkeypatch.delenv("EVAL_RUN_ID", raising=False)
    parameters = []
    monkeypatch.setattr("backend.app.llm.ChatOpenAI", lambda **kwargs: parameters.append(kwargs))
    create_model(reasoning_effort=effort, max_output_tokens=512).client("test-model", "test-provider")
    assert parameters[0]["extra_body"]["reasoning"] == {"effort": effort}
    assert parameters[0]["max_tokens"] == 512
    assert parameters[0]["max_retries"] == 0


def test_small_stage_limit_preserves_backup_model_output_budget(monkeypatch):
    monkeypatch.delenv("EVAL_RUN_ID", raising=False)
    parameters = []
    monkeypatch.setattr("backend.app.llm.ChatOpenAI", lambda **kwargs: parameters.append(kwargs))
    settings = get_settings()
    create_model(reasoning_effort="none", max_output_tokens=512).client(settings.openrouter_fallback_model, settings.openrouter_fallback_provider)
    assert parameters[0]["extra_body"]["reasoning"] == {"effort": "low"}
    assert parameters[0]["max_tokens"] == 4096


@pytest.mark.parametrize("field,value", [("version", "2.0 followed by a query"), ("version", "2.0, 1.0"), ("product", "2.0")])
def test_query_metadata_rejects_text_that_would_create_an_empty_filter(field, value):
    with pytest.raises(ValidationError):
        CustomerQueryDecision(decision="search", **{field: value})
