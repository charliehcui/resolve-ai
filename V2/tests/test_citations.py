from app.auth import authenticate
from app.citations import validate_claims
from app.docs import fetch_visible_chunks, import_product_docs
from app.models import AnswerClaim, CitationCheckOutput, ClaimCheck


def test_forged_citation_is_removed(seeded_database: dict[str, str], fake_embeddings: None) -> None:
    import_product_docs()
    auth = authenticate(seeded_database["token_a"])
    chunks = fetch_visible_chunks(auth)
    claim = AnswerClaim(text="伪造结论", cited_chunk_ids=["00000000-0000-0000-0000-000000000000"])
    supported, removed, _ = validate_claims([claim], chunks, auth, None)
    assert supported == []
    assert "引用不存在" in removed[0]


def test_other_company_cannot_validate_citation(seeded_database: dict[str, str], fake_embeddings: None) -> None:
    import_product_docs()
    auth_a = authenticate(seeded_database["token_a"])
    auth_b = authenticate(seeded_database["token_b"])
    chunks = fetch_visible_chunks(auth_a)
    claim = AnswerClaim(text="A 公司结论", cited_chunk_ids=[chunks[0].chunk_id])
    supported, _, _ = validate_claims([claim], chunks, auth_b, None)
    assert supported == []


def test_semantically_unsupported_claim_is_removed(seeded_database: dict[str, str], fake_embeddings: None, monkeypatch) -> None:
    import_product_docs()
    auth = authenticate(seeded_database["token_a"])
    chunks = fetch_visible_chunks(auth)
    claim = AnswerClaim(text="同步会自动补回所有历史订单", cited_chunk_ids=[chunks[0].chunk_id])

    def unsupported(*args, **kwargs):
        return CitationCheckOutput(checks=[ClaimCheck(claim_index=0, supported=False, reason="资料没有支持全部历史订单")]), {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}

    monkeypatch.setattr("app.citations.semantic_claim_checks", unsupported)
    supported, removed, usage = validate_claims([claim], chunks, auth, None)
    assert supported == []
    assert "没有支持" in removed[0]
    assert usage["total_tokens"] == 2
