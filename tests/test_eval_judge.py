"""Synthetic judge regressions; these are harness tests, not real LLM evaluation."""
from copy import deepcopy

import pytest

from evals.dataset import smoke_cases
from evals.evaluator import summarize
from evals.judge import JudgeError, RagJudgment, assess_rag_parts, empty_answer_scores, has_answer_content, judge_rag, resolve_references, rubric_text, source_quotes, validate_judgment
from evals.run import empty_result

DOCUMENTS = {"docs/product/test.md": "Administrators enable synchronization. Historical orders require separate approval."}
FACTS = ["Only administrators enable sync", "Historical orders need approval"]


def valid_judgment():
    evidence = [{"fact_index": 0, "source": "docs/product/test.md", "quote": "Administrators enable synchronization."}, {"fact_index": 1, "source": "docs/product/test.md", "quote": "Historical orders require separate approval."}]
    return {"answer_correct_before": True, "answer_correct_after": True, "claim_checks": [], "answer_evidence_before": evidence, "answer_evidence_after": deepcopy(evidence), "reason": "Synthetic test verdict"}


@pytest.mark.parametrize("answer", ["", "   ", "\n\t", "\u200b\ufeff", "** --- ...", "```json\n{}\n```", "N/A", "null", "[empty]"])
def test_empty_and_contentless_answers_cannot_be_correct_even_when_judge_says_true(answer):
    result = valid_judgment()
    result["answer_evidence_before"] = []
    result["answer_evidence_after"] = []
    assert not has_answer_content(answer)
    judged, overrides = validate_judgment(result, DOCUMENTS, [], answer, FACTS)
    assert not judged["answer_correct_before"]
    assert not judged["answer_correct_after"]
    assert len(overrides) == 2
    assert result["answer_correct_after"]  # 原始判决保留，不能手改原始结果。
    assert empty_answer_scores([], answer) == {"answer_accuracy_before": 0, "answer_accuracy_after": 0}


def test_valid_original_quotes_cover_required_facts_for_a_nonempty_answer():
    result = valid_judgment()
    result["answer_correct_before"] = False
    result["answer_evidence_before"] = []
    judged, _ = validate_judgment(result, DOCUMENTS, [], "Admin enables sync; history needs approval.", FACTS)
    assert judged["answer_correct_after"]


@pytest.mark.parametrize("change", ["fake_quote", "wrong_source", "blank_quote", "missing_fact", "duplicate_fact"])
def test_correct_answer_requires_exact_original_source_evidence_for_every_fact(change):
    result = valid_judgment()
    evidence = result["answer_evidence_after"]
    if change == "fake_quote":
        evidence[0]["quote"] = "Synchronization works for everyone."
    elif change == "wrong_source":
        evidence[0]["source"] = "docs/product/nonexistent.md"
    elif change == "blank_quote":
        evidence[0]["quote"] = " "
    elif change == "missing_fact":
        evidence.pop()
    else:
        evidence[1]["fact_index"] = 0
    with pytest.raises(ValueError):
        validate_judgment(result, DOCUMENTS, [], "Substantive answer", FACTS)


def test_even_an_incorrect_verdict_cannot_keep_fabricated_quotes():
    result = valid_judgment()
    result["answer_correct_after"] = False
    result["answer_evidence_after"][0]["quote"] = "Invented quote"
    with pytest.raises(ValueError, match="absent"):
        validate_judgment(result, DOCUMENTS, [], "Substantive answer", FACTS)


def test_supported_claims_need_real_quotes_and_all_claim_indices_are_checked():
    result = valid_judgment()
    result["claim_checks"] = [{"index": 0, "supported": True, "source": "docs/product/test.md", "quote": "Invented claim evidence", "reason": "Synthetic"}]
    with pytest.raises(ValueError, match="absent"):
        validate_judgment(result, DOCUMENTS, [{"text": "A claim"}], "Substantive answer", FACTS)
    result["claim_checks"] = []
    with pytest.raises(ValueError, match="every"):
        validate_judgment(result, DOCUMENTS, [{"text": "A claim"}], "Substantive answer", FACTS)


def test_judge_invocation_failure_is_typed_missing_evidence_not_an_answer_verdict(monkeypatch):
    class FailedModel:
        def with_structured_output(self, *args, **kwargs):
            return self

        def invoke(self, messages):
            raise RuntimeError("Synthetic provider failure")

    monkeypatch.setattr("evals.judge.create_model", lambda **kwargs: FailedModel())
    with pytest.raises(JudgeError) as error:
        judge_rag(smoke_cases()[0], [], [], "Nonempty answer")
    assert error.value.audit["status"] == "judge_error"
    assert error.value.audit["judgment"] is None
    assert error.value.audit["deterministic_metrics"] == {"answer_accuracy_before": 0.0}


def test_bad_quote_preserves_original_judge_verdict_and_marks_judge_error(monkeypatch):
    class FakeModel:
        def with_structured_output(self, *args, **kwargs):
            return self

        def invoke(self, messages):
            return {"parsed": RagJudgment.model_validate(valid_judgment()), "raw": None}

    monkeypatch.setattr("evals.judge.create_model", lambda **kwargs: FakeModel())
    with pytest.raises(JudgeError) as error:
        judge_rag(smoke_cases()[0], [], [], "Nonempty answer")
    assert error.value.audit["raw_judgment"]["answer_correct_after"]
    assert error.value.audit["judgment"] is None


def test_judge_failure_keeps_denominator_and_makes_full_accuracy_unknown():
    case = smoke_cases()[0]
    correct = empty_result(case, "vector_only", 1)
    correct["status"] = "passed"
    correct["metrics"] = {"answer_accuracy_after": 1.0}
    missing = empty_result(case, "hybrid", 1)
    missing["scoring_status"] = "judge_error"
    empty = empty_result(case, "hybrid_rerank", 1)
    empty["status"] = "failed"
    empty["metrics"] = {"answer_accuracy_after": 0.0}
    metric = summarize([correct, missing, empty])["metrics"]["answer_accuracy_after"]
    assert metric["value"] is None
    assert metric["numerator"] == 1
    assert metric["denominator"] == 3
    assert metric["measured_runs"] == 2
    assert metric["missing_evidence_runs"] == 1
    assert metric["measured_only_value"] == 0.5
    assert metric["accuracy_bounds"] == [1 / 3, 2 / 3]


@pytest.mark.parametrize("answer", ['```json {"answer":"Real content"}```', "```Actual content on one line.```", "```text\nReal content\n```"])
def test_substantive_fenced_answers_are_not_mistaken_for_empty(answer):
    assert has_answer_content(answer)


@pytest.mark.parametrize("stage", ["before", "after"])
def test_self_contradictory_judge_verdict_is_error_not_silently_correct_or_incorrect(stage):
    result = valid_judgment()
    result["answer_correct_before"] = stage == "before"
    result["answer_correct_after"] = stage == "after"
    result["claim_checks"] = [{"index": 0, "supported": False, "source": None, "quote": None, "reason": "Synthetic unsupported claim"}]
    before = [{"text": "A business claim"}]
    with pytest.raises(ValueError, match="contradiction"):
        validate_judgment(result, DOCUMENTS, before, "A business claim", FACTS, before)
    assert result["answer_correct_" + stage]  # 不改原始判决，也不猜哪一个冲突判决才正确。


def test_removing_unsupported_claims_does_not_make_a_consistent_correct_after_verdict_an_error():
    result = valid_judgment()
    result["answer_correct_before"] = False
    result["claim_checks"] = [{"index": 0, "supported": False, "source": None, "quote": None, "reason": "Synthetic unsupported claim"}]
    judgment, _ = validate_judgment(result, DOCUMENTS, [{"text": "Unsupported detail"}], "Grounded facts", FACTS, [])
    assert judgment["answer_correct_after"]


def test_quote_catalog_contains_exact_original_paragraphs_without_changing_sources():
    documents = {"a.md": "First original paragraph.\n\nSecond original paragraph.", "b.md": "Another original paragraph."}
    quotes = source_quotes(documents)
    assert len(quotes) == 3
    assert all(item["quote"] in documents[item["source"]] for item in quotes.values())


@pytest.mark.parametrize("identifiers", [["invented"], ["q1", "q1"]])
def test_unknown_or_duplicate_quote_references_are_judge_errors(identifiers):
    result = {"claim_checks": [{"index": 0, "supported": True, "evidence_ids": identifiers}], "answer_evidence_before": [], "answer_evidence_after": []}
    with pytest.raises(ValueError, match="unknown"):
        resolve_references(result, source_quotes({"a.md": "Original evidence."}))


def test_compound_claim_can_cite_separate_original_quotes_from_multiple_documents():
    documents = {"a.md": "Orders require approval.", "b.md": "Recovery requires an independent result check."}
    quotes = source_quotes(documents)
    reference = {"answer_correct_before": True, "answer_correct_after": True, "claim_checks": [{"index": 0, "supported": True, "evidence_ids": ["q1", "q2"], "reason": "Synthetic combined evidence"}], "answer_evidence_before": [{"fact_index": 0, "evidence_ids": ["q1", "q2"]}], "answer_evidence_after": [{"fact_index": 0, "evidence_ids": ["q1", "q2"]}], "reason": "Synthetic"}
    claims = [{"text": "Approval and result checking are required."}]
    resolved = resolve_references(reference, quotes)
    judgment, _ = validate_judgment(resolved, documents, claims, claims[0]["text"], ["Both are required"], claims)
    assert [item["source"] for item in judgment["claim_checks"][0]["evidence"]] == ["a.md", "b.md"]
    assert reference["claim_checks"][0].get("quote") is None


def test_optional_omission_does_not_hide_core_coverage():
    rubric = {"original_facts": ["The value is 7; the maintainer is a helpful contact."], "parts": [{"part_index": 0, "fact_index": 0, "text": "The value is 7", "core": True}, {"part_index": 1, "fact_index": 0, "text": "the maintainer is a helpful contact", "core": False}]}
    result = {"part_checks": [{"part_index": 0, "passed": True, "answer_quotes": ["value is 7"], "evidence_ids": ["q1"], "reason": "Value"}, {"part_index": 1, "passed": False, "answer_quotes": [], "evidence_ids": [], "reason": "Unasked contact absent"}]}
    scored = assess_rag_parts(result, rubric, "The value is 7.")
    assert scored["core_facts_complete"]
    assert not scored["fact_checks"][0]["passed"]
    assert scored["core_fact_coverage"] == {"numerator": 1, "denominator": 1}
    assert scored["all_fact_coverage"] == {"numerator": 1, "denominator": 2}


@pytest.mark.parametrize("quotes", [[], ["Contact Support"], [""]])
def test_judge_cannot_pass_a_fact_using_content_absent_from_answer(quotes):
    rubric = {"original_facts": ["Contact Support"], "parts": [{"part_index": 0, "fact_index": 0, "text": "Contact Support", "core": True}]}
    result = {"part_checks": [{"part_index": 0, "passed": True, "answer_quotes": quotes, "evidence_ids": ["q1"], "reason": "Contact exists in document"}]}
    with pytest.raises(ValueError):
        assess_rag_parts(result, rubric, "No fixed SLA is documented.")


def test_rubric_normalization_cannot_erase_factual_words_or_numbers():
    assert rubric_text("A；B， 3.0") == rubric_text("A B 3.0")
    assert rubric_text("A B 3.0") != rubric_text("A B 2.0")


def test_coverage_judge_sees_only_answer_and_recovers_fabricated_quote_once(monkeypatch):
    from evals.judge import rag_answer_coverage
    replies = iter([{"part_checks": [{"part_index": 0, "passed": True, "answer_ids": ["invented"], "reason": "Invented proof"}]}, {"part_checks": [{"part_index": 0, "passed": False, "answer_ids": [], "reason": "Unasked step absent"}]}])
    calls = []
    class FakeModel:
        def with_structured_output(self, *args, **kwargs):
            return self
        def invoke(self, messages):
            calls.append(messages)
            return {"parsed": next(replies), "raw": None}
    monkeypatch.setattr("evals.judge.create_model", lambda **kwargs: FakeModel())
    rubric = {"parts": [{"part_index": 0, "text": "Contact Support", "core": False}]}
    judged, _, events = rag_answer_coverage("What is documented?", rubric, "No fixed deadline is documented.")
    assert not judged["part_checks"][0]["passed"]
    assert len(calls) == 2
    assert "original_source_quotes" not in calls[0][1].content
    assert events[-1]["attempt"] == 2
