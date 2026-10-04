from types import SimpleNamespace

import pytest

from backend.app.citations import StructuredOutputError, invoke_structured, validate_claims
from backend.app.customer_agent import complete_answer_claims, generate_answer_from_documents
from backend.app.models import AnswerClaim, CustomerClaimReview, CustomerGeneratedClaims, CustomerQuestionCheck, RetrievedChunk, UserContext


@pytest.fixture(scope="session", autouse=True)
def isolated_test_database():
    yield


class Replies:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = 0

    def with_structured_output(self, *args, **kwargs):
        return self

    def invoke(self, messages):
        self.calls += 1
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return {"parsed": reply, "raw": SimpleNamespace(content="", usage_metadata={"input_tokens": 2, "output_tokens": 1, "total_tokens": 3})}


def test_structured_recovery_stops_after_one_extra_attempt():
    model = Replies([None, None])
    with pytest.raises(StructuredOutputError) as error:
        invoke_structured(model, CustomerGeneratedClaims.model_json_schema(), CustomerGeneratedClaims, [], "answer_generation")
    assert model.calls == 2
    assert len(error.value.events) == 2
    assert error.value.usage["total_tokens"] == 6


def test_valid_json_body_is_recovered_without_an_extra_model_call():
    model = Replies([])
    model.invoke = lambda messages: {"parsed": None, "raw": SimpleNamespace(content='{"claims": []}', usage_metadata={})}
    output, _, events = invoke_structured(model, CustomerGeneratedClaims.model_json_schema(), CustomerGeneratedClaims, [], "answer_generation")
    assert output.claims == []
    assert events[0]["recovery"] == "raw_json"


def test_provider_failure_is_not_reclassified_as_unsupported_claims():
    model = Replies([RuntimeError("Upstream provider failure")])
    with pytest.raises(RuntimeError, match="provider"):
        invoke_structured(model, CustomerGeneratedClaims.model_json_schema(), CustomerGeneratedClaims, [], "answer_generation")
    assert model.calls == 1


def test_valid_atomic_claim_survives_while_a_sibling_is_rejected(monkeypatch):
    chunk = RetrievedChunk(chunk_id="00000000-0000-0000-0000-000000000001", title="Rules", source_uri="rules.md", version="2.0", content="Accepted does not mean completed.", score=1)
    claims = [AnswerClaim(text="Acceptance is not completion.", cited_chunk_ids=[chunk.chunk_id], evidence_quote="Accepted does not mean completed."), AnswerClaim(text="It automatically retries.", cited_chunk_ids=[chunk.chunk_id])]
    monkeypatch.setattr("backend.app.citations.authorized_chunk_ids", lambda *args: {chunk.chunk_id})
    from backend.app.models import ClaimValidationOutput, ClaimValidationResult
    checks = ClaimValidationOutput(checks=[ClaimValidationResult(claim_index=0, supported=True, reason="Rule"), ClaimValidationResult(claim_index=1, supported=False, reason="No automatic retry")])
    monkeypatch.setattr("backend.app.citations.semantic_claim_checks", lambda *args: (checks, {}))
    accepted, removed, _ = validate_claims(claims, [chunk], UserContext(company_id="test", user_id="u", role="staff"), "2.0", "Does it retry?")
    assert accepted == [claims[0]] and len(removed) == 1


def test_every_accepted_claim_reaches_final_answer_and_validator_gets_question(monkeypatch):
    chunk = RetrievedChunk(chunk_id="00000000-0000-0000-0000-000000000001", title="Rules", source_uri="rules.md", version="2.0", content="First fact. Second fact.", score=1)
    claims = [AnswerClaim(text="First fact.", cited_chunk_ids=[chunk.chunk_id]), AnswerClaim(text="Second fact.", cited_chunk_ids=[chunk.chunk_id])]
    model = Replies([CustomerGeneratedClaims(claims=claims), CustomerClaimReview(question_checks=[CustomerQuestionCheck(question="Which facts apply?", cited_chunk_ids=[chunk.chunk_id], evidence_quote=chunk.content, draft_claim_indices=[0, 1], complete=True)], missing_answers=[], remove_claim_indices=[], claims=[])])
    monkeypatch.setattr("backend.app.customer_agent.create_model", lambda **kwargs: model)
    received = []

    def validate(items, chunks, user, version, question, history):
        received.append((question, history))
        return items, [], {}

    monkeypatch.setattr("backend.app.customer_agent.validate_claims", validate)
    answer = generate_answer_from_documents("Which facts apply?", [chunk], [], UserContext(company_id="test", user_id="u", role="staff"), "2.0")
    assert answer.answer == "First fact.\nSecond fact."
    assert received == [("Which facts apply?", [])]


def test_noncontiguous_table_anchor_still_gets_real_evidence_validation(monkeypatch):
    chunk = RetrievedChunk(chunk_id="00000000-0000-0000-0000-000000000001", title="States", source_uri="states.xlsx", version="2.0", content="State: open\nMeaning: waiting\nBoundary: not resolved", score=1)
    claim = AnswerClaim(text="Open means waiting.", cited_chunk_ids=[chunk.chunk_id], evidence_quote="State: open; Meaning: waiting")
    monkeypatch.setattr("backend.app.citations.authorized_chunk_ids", lambda *args: {chunk.chunk_id})
    received = []
    def check(claims, chunks, *args):
        from backend.app.models import ClaimValidationOutput, ClaimValidationResult
        received.append((claims[0].evidence_quote, chunks[0].content))
        return ClaimValidationOutput(checks=[ClaimValidationResult(claim_index=0, supported=True, reason="Actual table supports it")]), {}
    monkeypatch.setattr("backend.app.citations.semantic_claim_checks", check)
    accepted, removed, _ = validate_claims([claim], [chunk], UserContext(company_id="test", user_id="u", role="staff"), "2.0")
    assert [item.text for item in accepted] == [claim.text]
    assert not removed
    assert received == [("", chunk.content)]


def test_bundled_sentences_are_verified_independently_before_final_composition(monkeypatch):
    from backend.app.models import ClaimValidationOutput, ClaimValidationResult
    chunk = RetrievedChunk(chunk_id="00000000-0000-0000-0000-000000000001", title="Rules", source_uri="rules.md", version="2.0", content="受理不等于完成。", score=1)
    claim = AnswerClaim(text="受理不等于完成。系统会自动重试。", cited_chunk_ids=[chunk.chunk_id], evidence_quote=chunk.content)
    model = Replies([CustomerGeneratedClaims(claims=[claim]), CustomerClaimReview(question_checks=[CustomerQuestionCheck(question="受理代表完成吗？", cited_chunk_ids=[chunk.chunk_id], evidence_quote=chunk.content, draft_claim_indices=[0], complete=True)], missing_answers=[], remove_claim_indices=[], claims=[])])
    monkeypatch.setattr("backend.app.customer_agent.create_model", lambda **kwargs: model)
    monkeypatch.setattr("backend.app.citations.authorized_chunk_ids", lambda *args: {chunk.chunk_id})
    def check(claims, *args):
        assert [item.text for item in claims] == ["受理不等于完成。", "系统会自动重试。"]
        return ClaimValidationOutput(checks=[ClaimValidationResult(claim_index=0, supported=True, reason="Documented boundary"), ClaimValidationResult(claim_index=1, supported=False, reason="No retry guarantee")]), {}
    monkeypatch.setattr("backend.app.citations.semantic_claim_checks", check)
    answer = generate_answer_from_documents("受理代表完成吗？", [chunk], [], UserContext(company_id="test", user_id="u", role="staff"), "2.0")
    assert answer.answer == "受理不等于完成。"
    assert len(answer.removed_claims) == 1


def test_completeness_additions_are_validated_and_usage_is_accounted(monkeypatch):
    chunk = RetrievedChunk(chunk_id="00000000-0000-0000-0000-000000000001", title="Rules", source_uri="rules.md", version="2.0", content="受理不等于完成。完成需要复查。", score=1)
    initial = AnswerClaim(text="受理不等于完成。", cited_chunk_ids=[chunk.chunk_id], evidence_quote="受理不等于完成。")
    addition = AnswerClaim(text="完成需要复查。", cited_chunk_ids=[chunk.chunk_id], evidence_quote="完成需要复查。")
    invention = AnswerClaim(text="会自动复查。", cited_chunk_ids=[chunk.chunk_id], evidence_quote="完成需要复查。")
    model = Replies([CustomerGeneratedClaims(claims=[initial]), CustomerClaimReview(question_checks=[CustomerQuestionCheck(question="如何确认完成？", cited_chunk_ids=[chunk.chunk_id], evidence_quote=chunk.content, draft_claim_indices=[], complete=False)], missing_answers=["如何确认完成？"], remove_claim_indices=[], claims=[addition, invention])])
    monkeypatch.setattr("backend.app.customer_agent.create_model", lambda **kwargs: model)
    def validate(claims, *args):
        assert claims == [initial, addition, invention]
        return claims[:2], [invention.text], {"total_tokens": 5}
    monkeypatch.setattr("backend.app.customer_agent.validate_claims", validate)
    answer = generate_answer_from_documents("受理代表完成吗？如何确认完成？", [chunk], [], UserContext(company_id="test", user_id="u", role="staff"), "2.0")
    assert model.calls == 2
    assert answer.answer == "受理不等于完成。\n完成需要复查。"
    assert answer.removed_claims == [invention.text]
    assert answer.usage["total_tokens"] == 11


def test_completeness_review_preserves_correct_claims_and_receives_no_ground_truth():
    chunk = RetrievedChunk(chunk_id="00000000-0000-0000-0000-000000000001", title="Rules", source_uri="rules.md", version="2.0", content="读取结果不是流程结果。", score=1)
    correct = AnswerClaim(text="可以读取。", cited_chunk_ids=[chunk.chunk_id])
    wrong = AnswerClaim(text="流程结果是读取错误。", cited_chunk_ids=[chunk.chunk_id])
    replacement = AnswerClaim(text="读取结果不是流程结果。", cited_chunk_ids=[chunk.chunk_id])
    model = Replies([])
    def invoke(messages):
        import json
        data = json.loads(messages[1].content)
        assert set(data) == {"question", "recent_history", "retrieved_context", "draft_claims"}
        assert "required_facts" not in data and "expected_facts" not in data
        assert data["draft_claims"][1]["index"] == 1
        return {"parsed": CustomerClaimReview(question_checks=[CustomerQuestionCheck(question="流程结果？", cited_chunk_ids=[chunk.chunk_id], evidence_quote=chunk.content, draft_claim_indices=[1], complete=False)], missing_answers=["流程结果？"], remove_claim_indices=[1], claims=[replacement]), "raw": SimpleNamespace(usage_metadata={})}
    model.invoke = invoke
    completed, _, _ = complete_answer_claims("可以读取吗？流程结果？", [chunk], [], [correct, wrong], model)
    assert completed == [correct, replacement]


def test_completeness_review_unknown_citation_stops_after_one_recovery():
    chunk = RetrievedChunk(chunk_id="00000000-0000-0000-0000-000000000001", title="Rules", source_uri="rules.md", version="2.0", content="Known rule.", score=1)
    invalid = CustomerClaimReview(question_checks=[CustomerQuestionCheck(question="Gap?", cited_chunk_ids=[chunk.chunk_id], evidence_quote=chunk.content, draft_claim_indices=[], complete=False)], missing_answers=["Gap"], remove_claim_indices=[], claims=[AnswerClaim(text="Invention", cited_chunk_ids=["unknown"])])
    model = Replies([invalid, invalid])
    with pytest.raises(StructuredOutputError):
        complete_answer_claims("Question?", [chunk], [], [], model)
    assert model.calls == 2


def test_review_can_rebind_correct_text_without_dropping_its_fact():
    chunk = RetrievedChunk(chunk_id="00000000-0000-0000-0000-000000000001", title="Rules", source_uri="rules.md", version="2.0", content="最近成功不保证永久成功。", score=1)
    initial = AnswerClaim(text=chunk.content, cited_chunk_ids=["wrong"])
    replacement = AnswerClaim(text=initial.text, cited_chunk_ids=[chunk.chunk_id], evidence_quote=chunk.content)
    review = CustomerClaimReview(question_checks=[CustomerQuestionCheck(question="成功是永久的吗？", cited_chunk_ids=[chunk.chunk_id], evidence_quote=chunk.content, draft_claim_indices=[0], complete=False)], missing_answers=[], remove_claim_indices=[0], claims=[replacement])
    completed, _, _ = complete_answer_claims("成功是永久的吗？", [chunk], [], [initial], Replies([review]))
    assert completed == [replacement]
