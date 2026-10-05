import pytest

from backend.app.config import PROJECT_ROOT
from evals.dataset import load_cases
from evals.evaluator import rag_retrieval_scores
from evals.run import select_cases


def test_sources_are_and_groups_with_or_alternatives_and_actual_five_ranks():
    case = {"expected": {"source_groups": [["a.pdf", "b.docx"], ["codes.xlsx"]]}, "claim_ground_truth": {"source_evidence": {"a.pdf": ["approval expires"], "b.docx": ["approval expires"], "codes.xlsx": ["E_MAP_MISSING"]}}}
    first = {"chunk_id": "1", "source_uri": "b.docx", "content": "approval expires"}
    irrelevant = {"chunk_id": "2", "source_uri": "codes.xlsx", "content": "E_AUTH_EXPIRED"}
    correct = {"chunk_id": "3", "source_uri": "codes.xlsx", "content": "E_MAP_MISSING"}
    scored = rag_retrieval_scores([irrelevant, first, first, first, first, correct], case)
    assert scored["recall_at_5"] == 0.5
    assert scored["mrr"] == 0.5
    assert len(scored["retrieval_evidence"]) == 5
    assert not scored["retrieval_evidence"][0]["topic_match"]
    assert rag_retrieval_scores([first, correct], case)["recall_at_5"] == 1


def test_rag_stage_isolation_and_baseline_mode():
    cases = load_cases(PROJECT_ROOT / "evals/data/smoke.jsonl")
    development, modes = select_cases(cases, "quick", "rag", None, None, rag_stage="baseline")
    assert len(development) == 30 and modes == ["vector_only"]
    assert all(case.expected["split"] == "development" for case in development)
    holdout, _ = select_cases(cases, "quick", "rag", None, ["hybrid"], rag_stage="holdout")
    assert len(holdout) == 10
    with pytest.raises(ValueError, match="Holdout"):
        select_cases(cases, "quick", "rag", [holdout[0].case_id], None)
    with pytest.raises(ValueError, match="vector_only"):
        select_cases(cases, "quick", "rag", None, ["hybrid"], rag_stage="baseline")


def test_text_topic_evidence_can_be_paraphrased_but_metadata_alone_is_not_relevant():
    case = {"expected": {"source_groups": [["rules.md"]], "expected_topic": "管理员批准同步开关"}, "claim_ground_truth": {"source_evidence": {"rules.md": ["原文中的一个完整证据句"]}}}
    related = {"chunk_id": "a", "source_uri": "rules.md", "content": "标题：同步开关\n\n必须由管理员批准，之后才能修改同步开关。"}
    unrelated = {"chunk_id": "b", "source_uri": "rules.md", "content": "标题：管理员批准同步开关\n\n发货单只记录物流单号。"}
    assert rag_retrieval_scores([related], case)["recall_at_5"] == 1
    assert rag_retrieval_scores([unrelated], case)["recall_at_5"] == 0


def test_hybrid_source_diversity_preserves_top_result_and_fills_small_pools():
    from backend.app.customer_retrieval import select_source_chunks
    from backend.app.models import RetrievedChunk
    chunks = [RetrievedChunk(chunk_id=str(index), source_uri=source, title=source, version="2.0", content="Document body", score=1) for index, source in enumerate(["a", "a", "a", "b", "b", "c"])]
    assert [chunk.chunk_id for chunk in select_source_chunks(chunks)] == ["0", "1", "3", "4", "5"]
    same_source = [chunk.model_copy(update={"source_uri": "a"}) for chunk in chunks]
    assert select_source_chunks(same_source) == same_source[:5]


@pytest.mark.parametrize("gap_correct", [True, False, None])
def test_no_answer_requires_a_real_boolean_and_every_required_fact(monkeypatch, gap_correct):
    from evals.judge import JudgeError, judge_rag
    documents = {"rules.md": "Support investigates tickets. No fixed service deadline is documented."}
    quotes = {"q1": {"source": "rules.md", "quote": documents["rules.md"]}}
    monkeypatch.setattr("evals.judge.rag_source_catalog", lambda *args: (documents, quotes))
    rubric = {"original_facts": ["No documented fixed deadline", "Support investigates tickets"], "parts": [{"part_index": 0, "fact_index": 0, "text": "No documented fixed deadline", "core": True}, {"part_index": 1, "fact_index": 1, "text": "Support investigates tickets", "core": True}]}
    monkeypatch.setattr("evals.judge.rag_rubric", lambda *args: rubric)
    claims = [{"text": "Support investigates tickets."}, {"text": "No fixed service deadline is documented."}]
    coverage = {"part_checks": [{"part_index": 0, "passed": True, "answer_quotes": [claims[1]["text"]], "reason": "Synthetic documented gap"}, {"part_index": 1, "passed": True, "answer_quotes": [claims[0]["text"]], "reason": "Synthetic known fact"}]}
    monkeypatch.setattr("evals.judge.rag_answer_coverage", lambda *args: (coverage, {}, []))
    payload = {"answer_correct_before": False, "claim_checks": [{"index": index, "supported": True, "evidence_ids": ["q1"], "reason": "Original source supports statement"} for index in range(2)], "answer_evidence_before": [], "forbidden_claims": [], "unsupported_final_claims": [], "no_answer_correct": gap_correct, "reason": "Synthetic contract check"}
    class FakeModel:
        def with_structured_output(self, schema, **kwargs):
            assert schema["$defs"]["ClaimReference"]["properties"]["evidence_ids"]["items"]["enum"] == ["q1"]
            assert schema["properties"]["claim_checks"]["maxItems"] == 2
            return self
        def invoke(self, messages):
            return {"parsed": payload, "raw": None}
    monkeypatch.setattr("evals.judge.create_model", lambda **kwargs: FakeModel())
    case = {"question": "What is the exact deadline?", "expected": {"answerable": False}, "claim_ground_truth": {"required_facts": ["No documented fixed deadline", "Support investigates tickets"], "forbidden_claims": ["Promises a deadline"]}}
    if gap_correct is None:
        with pytest.raises(JudgeError, match="grounding_judge") as error:
            judge_rag(case, claims, claims, documents["rules.md"])
        assert len(error.value.audit["structured_output"]) == 2
    else:
        audit = judge_rag(case, claims, claims, documents["rules.md"])
        assert audit["judgment"]["answer_correct_after"] is gap_correct
        assert audit["raw_judgment"]["no_answer_correct"] is gap_correct


@pytest.mark.parametrize("core_passed", [True, False])
def test_optional_completeness_does_not_override_audited_task_success(monkeypatch, core_passed):
    from evals.execute import score_agent
    monkeypatch.setattr("evals.execute.score_observed_tools_and_retrieval", lambda *args: None)
    judgment = {"answer_correct_before": False, "answer_correct_after": core_passed, "claim_checks": [], "unsupported_final_claims": [], "forbidden_claims": [], "fact_checks": [{"fact_index": 0, "passed": core_passed, "core_required": True, "core_passed": core_passed, "reason": "Core answer"}, {"fact_index": 1, "passed": False, "core_required": False, "core_passed": None, "reason": "Unasked contact"}]}
    monkeypatch.setattr("evals.execute.judge_rag", lambda *args: {"judgment": judgment})
    case = {"category": "rag", "question": "What is the value?", "expected_handoff": False, "expected": {"answerable": True}}
    output = {"turn": {"answer": "The documented value is 7."}, "observations": {"before_claims": [], "after_claims": [], "retrieval": []}, "metrics": {}, "failure_reasons": []}
    score_agent(case, output)
    assert output["failure_reasons"] == ([] if core_passed else ["Missing core fact: Core answer"])
