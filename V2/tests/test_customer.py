from backend.app.customer_agent import decide_customer_query_next_step
from backend.app.models import CustomerQueryDecision


class FakeStructuredModel:
    def __init__(self) -> None:
        self.calls = 0

    def with_structured_output(self, *args, **kwargs):
        return self

    def invoke(self, messages):
        self.calls += 1
        return {"parsed": CustomerQueryDecision(decision="search", search_query="ORDER_SYNC_DISABLED", rewrite_used=True), "raw": object()}


def test_query_is_rewritten_at_most_once(monkeypatch) -> None:
    model = FakeStructuredModel()
    monkeypatch.setattr("backend.app.customer_agent.create_groq_model", lambda: model)
    query_decision, _ = decide_customer_query_next_step("单子不进来", [])
    assert query_decision.rewrite_used is True
    assert model.calls == 1


def test_legacy_question_without_version_is_clarified(monkeypatch) -> None:
    model = FakeStructuredModel()
    monkeypatch.setattr("backend.app.customer_agent.create_groq_model", lambda: model)
    query_decision, _ = decide_customer_query_next_step("旧版里的同步入口在哪里？", [])
    assert query_decision.decision == "clarify"
    assert "版本号" in query_decision.customer_message


def test_handoff_message_claims_only_the_real_role_transfer(monkeypatch) -> None:
    model = FakeStructuredModel()
    model.invoke = lambda messages: {"parsed": CustomerQueryDecision(decision="handoff", customer_message="Forwarded."), "raw": object()}
    monkeypatch.setattr("backend.app.customer_agent.create_groq_model", lambda: model)
    query_decision, _ = decide_customer_query_next_step("订单 O-1001 当前在哪里？", [])
    assert query_decision.decision == "handoff"
    assert "已转交 Support Agent" in query_decision.customer_message
    assert "Customer Agent 没有读取后台数据" in query_decision.customer_message
