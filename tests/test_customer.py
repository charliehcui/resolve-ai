import pytest
from pydantic import ValidationError

from backend.app.customer_agent import decide_customer_query_next_step
from backend.app.models import CustomerQueryDecision


@pytest.fixture(scope="session", autouse=True)
def isolated_test_database():
    yield


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
    monkeypatch.setattr("backend.app.customer_agent.create_model", lambda **kwargs: model)
    query_decision, _ = decide_customer_query_next_step("单子不进来", [])
    assert query_decision.rewrite_used is True
    assert model.calls == 1


def test_legacy_question_without_version_is_clarified(monkeypatch) -> None:
    model = FakeStructuredModel()
    monkeypatch.setattr("backend.app.customer_agent.create_model", lambda **kwargs: model)
    query_decision, _ = decide_customer_query_next_step("旧版里的同步入口在哪里？", [])
    assert query_decision.decision == "clarify"
    assert "版本号" in query_decision.customer_message


def test_handoff_message_claims_only_the_real_role_transfer(monkeypatch) -> None:
    model = FakeStructuredModel()
    model.invoke = lambda messages: {"parsed": CustomerQueryDecision(decision="handoff", customer_message="Forwarded."), "raw": object()}
    monkeypatch.setattr("backend.app.customer_agent.create_model", lambda **kwargs: model)
    query_decision, _ = decide_customer_query_next_step("订单 O-1001 当前在哪里？", [])
    assert query_decision.decision == "handoff"
    assert "已转交 Support Agent" in query_decision.customer_message
    assert "Customer Agent 没有读取后台数据" in query_decision.customer_message


def test_authenticated_company_is_never_a_product_filter(monkeypatch):
    model = FakeStructuredModel()
    model.invoke = lambda messages: {"parsed": CustomerQueryDecision(decision="search", search_query="General rules", product="tenant-test", version="2.0"), "raw": object()}
    monkeypatch.setattr("backend.app.customer_agent.create_model", lambda **kwargs: model)
    decision, _ = decide_customer_query_next_step("Explain tenant-test product rules", [], "tenant-test")
    assert decision.product is None and decision.version == "2.0"


def test_explicit_unknown_product_is_not_broadened(monkeypatch):
    model = FakeStructuredModel()
    model.invoke = lambda messages: {"parsed": CustomerQueryDecision(decision="search", search_query="Other product", product="unknown-product", version="3.0"), "raw": object()}
    monkeypatch.setattr("backend.app.customer_agent.create_model", lambda **kwargs: model)
    decision, _ = decide_customer_query_next_step("Other product", [], "tenant-test")
    assert decision.product == "unknown-product" and decision.version == "3.0"


def test_malformed_citation_never_reaches_uuid_database_query(monkeypatch):
    from backend.app.citations import validate_claims
    from backend.app.models import AnswerClaim, RetrievedChunk, UserContext
    checked = []
    monkeypatch.setattr("backend.app.citations.authorized_chunk_ids", lambda identifiers, *args: checked.append(identifiers) or set())
    claim = AnswerClaim(text="A forged claim", cited_chunk_ids=["bad-short-id"])
    chunk = RetrievedChunk(chunk_id="00000000-0000-0000-0000-000000000001", title="rules", source_uri="rules.md", version="2.0", content="Original fact", score=1)
    supported, removed, _ = validate_claims([claim], [chunk], UserContext(user_id="u", company_id="tenant", role="staff"), "2.0")
    assert checked == [[]] and not supported and removed


def test_standalone_compound_questions_preserve_the_exact_query(monkeypatch):
    model = FakeStructuredModel()
    monkeypatch.setattr("backend.app.customer_agent.create_model", lambda **kwargs: model)
    question = "状态变了应如何解释？之前的处理是否仍然有效？"
    decision, _ = decide_customer_query_next_step(question, [])
    assert decision.search_query == question and not decision.rewrite_used and model.calls == 1


@pytest.mark.parametrize("payload", [{"decision": "handoff"}, {"action": "handoff"}, {"decision": "handoff", "action": "handoff"}])
def test_explicit_handoff_field_alias_preserves_customer_routing(monkeypatch, payload):
    original = dict(payload)
    model = FakeStructuredModel()
    model.invoke = lambda messages: {"parsed": CustomerQueryDecision.model_validate(payload, strict=True), "raw": object()}
    monkeypatch.setattr("backend.app.customer_agent.create_model", lambda **kwargs: model)
    decision, _ = decide_customer_query_next_step("查询订单 O-1001 的后台状态", [])
    assert decision.decision == "handoff"
    assert "已转交 Support Agent" in decision.customer_message
    assert payload == original
    assert "decision" in CustomerQueryDecision.model_json_schema()["required"]


@pytest.mark.parametrize("payload", [{}, {"customer_message": "Please contact Support"}, {"action": "unknown"}, {"action": "search"}, {"action": "handoff", "decision": "search"}, {"action": "handoff", "decision": None}])
def test_handoff_normalization_never_invents_or_overrides_a_decision(payload):
    with pytest.raises(ValidationError):
        CustomerQueryDecision.model_validate(payload, strict=True)
