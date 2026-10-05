from types import SimpleNamespace

import pytest

from backend.app.citations import StructuredOutputError, invoke_structured, validate_claims
from backend.app.customer_agent import generate_answer_from_documents
from backend.app.models import AnswerClaim, CustomerGeneratedClaims, RetrievedChunk, UserContext


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


def test_generation_is_one_call_and_rejects_a_sibling_without_a_real_quote(monkeypatch):
    chunk = RetrievedChunk(chunk_id="00000000-0000-0000-0000-000000000001", title="Rules", source_uri="rules.md", version="2.0", content="Accepted does not mean completed.", score=1)
    claims = [AnswerClaim(text="Acceptance is not completion.", cited_chunk_ids=[chunk.chunk_id], evidence_quote=chunk.content), AnswerClaim(text="It automatically retries.", cited_chunk_ids=[chunk.chunk_id], evidence_quote="Automatic retries")]
    model = Replies([CustomerGeneratedClaims(subquestions=["Does acceptance mean completion?"], claims=claims)])
    monkeypatch.setattr("backend.app.customer_agent.create_model", lambda **kwargs: model)
    monkeypatch.setattr("backend.app.citations.authorized_chunk_ids", lambda *args: {chunk.chunk_id})
    answer = generate_answer_from_documents("Does acceptance mean completion?", [chunk], [], UserContext(company_id="test", user_id="u", role="staff"), "2.0")
    assert model.calls == 1
    assert answer.claims == [claims[0]] and len(answer.removed_claims) == 1
    assert answer.usage["total_tokens"] == 3


@pytest.mark.parametrize("quote", ["", "State: open; Meaning: waiting", "Boundary: resolved"])
def test_missing_or_noncontiguous_anchors_are_rejected_without_a_model(monkeypatch, quote):
    chunk = RetrievedChunk(chunk_id="00000000-0000-0000-0000-000000000001", title="States", source_uri="states.xlsx", version="2.0", content="State: open\nMeaning: waiting\nBoundary: not resolved", score=1)
    claim = AnswerClaim(text="Open means waiting.", cited_chunk_ids=[chunk.chunk_id], evidence_quote=quote)
    monkeypatch.setattr("backend.app.citations.authorized_chunk_ids", lambda *args: {chunk.chunk_id})
    accepted, removed, usage = validate_claims([claim], [chunk], UserContext(company_id="test", user_id="u", role="staff"), "2.0")
    assert not accepted and len(removed) == 1 and usage == {}


def test_no_remaining_claims_produce_an_honest_unconfirmed_answer(monkeypatch):
    chunk = RetrievedChunk(chunk_id="00000000-0000-0000-0000-000000000001", title="Rules", source_uri="rules.md", version="2.0", content="Known fact.", score=1)
    model = Replies([CustomerGeneratedClaims(claims=[AnswerClaim(text="Unknown fact.", cited_chunk_ids=[chunk.chunk_id], evidence_quote="Not in the source")])])
    monkeypatch.setattr("backend.app.customer_agent.create_model", lambda **kwargs: model)
    monkeypatch.setattr("backend.app.citations.authorized_chunk_ids", lambda *args: {chunk.chunk_id})
    answer = generate_answer_from_documents("What is known?", [chunk], [], UserContext(company_id="test", user_id="u", role="staff"), "2.0")
    assert answer.needs_support and not answer.claims and not answer.citations
    assert model.calls == 1
