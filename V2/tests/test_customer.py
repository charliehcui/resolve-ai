from app.customer import plan_customer_query
from app.models import CustomerQueryPlan


class FakeStructuredModel:
    def __init__(self) -> None:
        self.calls = 0

    def with_structured_output(self, *args, **kwargs):
        return self

    def invoke(self, messages):
        self.calls += 1
        return {"parsed": CustomerQueryPlan(decision="search", search_query="ORDER_SYNC_DISABLED", rewrite_used=True), "raw": object()}


def test_query_is_rewritten_at_most_once(monkeypatch) -> None:
    model = FakeStructuredModel()
    monkeypatch.setattr("app.customer.create_groq_model", lambda: model)
    plan, _ = plan_customer_query("单子不进来", [])
    assert plan.rewrite_used is True
    assert model.calls == 1


def test_legacy_question_without_version_is_clarified(monkeypatch) -> None:
    model = FakeStructuredModel()
    monkeypatch.setattr("app.customer.create_groq_model", lambda: model)
    plan, _ = plan_customer_query("旧版里的同步入口在哪里？", [])
    assert plan.decision == "clarify"
    assert "版本号" in plan.customer_message


def test_handoff_message_claims_only_the_real_role_transfer(monkeypatch) -> None:
    model = FakeStructuredModel()
    model.invoke = lambda messages: {"parsed": CustomerQueryPlan(decision="handoff", customer_message="Forwarded."), "raw": object()}
    monkeypatch.setattr("app.customer.create_groq_model", lambda: model)
    plan, _ = plan_customer_query("订单 O-1001 当前在哪里？", [])
    assert plan.decision == "handoff"
    assert "已转交 Support Agent" in plan.customer_message
    assert "Customer Agent 没有读取后台数据" in plan.customer_message
