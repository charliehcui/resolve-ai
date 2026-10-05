from backend.app.auth import authenticate
from backend.app.citations import validate_claims
from backend.app.customer_document_ingestion import import_product_documents
from backend.app.customer_retrieval import fetch_visible_chunks
from backend.app.models import AnswerClaim


def test_forged_citation_is_removed(seeded_database: dict[str, str], fake_embeddings: None) -> None:
    import_product_documents()
    user = authenticate(seeded_database["token_a"])
    chunks = fetch_visible_chunks(user)
    claim = AnswerClaim(text="伪造结论", cited_chunk_ids=["00000000-0000-0000-0000-000000000000"])
    supported, removed, _ = validate_claims([claim], chunks, user, None)
    assert supported == []
    assert "引用不存在" in removed[0]


def test_other_company_cannot_validate_citation(seeded_database: dict[str, str], fake_embeddings: None) -> None:
    import_product_documents()
    auth_a = authenticate(seeded_database["token_a"])
    auth_b = authenticate(seeded_database["token_b"])
    chunks = fetch_visible_chunks(auth_a)
    claim = AnswerClaim(text="A 公司结论", cited_chunk_ids=[chunks[0].chunk_id])
    supported, _, _ = validate_claims([claim], chunks, auth_b, None)
    assert supported == []


def test_fabricated_evidence_quote_is_removed_without_an_llm(seeded_database: dict[str, str], fake_embeddings: None) -> None:
    import_product_documents()
    user = authenticate(seeded_database["token_a"])
    chunks = fetch_visible_chunks(user)
    claim = AnswerClaim(text="同步会自动补回所有历史订单", cited_chunk_ids=[chunks[0].chunk_id], evidence_quote="Automatically backfill every historical order forever.")
    supported, removed, usage = validate_claims([claim], chunks, user, None)
    assert supported == []
    assert "原文锚点" in removed[0]
    assert usage == {}
