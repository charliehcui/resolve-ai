from app.auth import authenticate
from app.db import get_connection
from app.docs import PRODUCT_DOCS_DIR, import_product_docs, parse_product_doc, reciprocal_rank_fusion, retrieve_documents, tokenize
from app.models import RetrievedChunk


def test_all_product_docs_have_required_metadata() -> None:
    documents = [parse_product_doc(path) for path in sorted(PRODUCT_DOCS_DIR.glob("*.md"))]
    assert len(documents) == 14
    assert all(document["company_id"] == "company-a" for document in documents)
    assert {document["version"] for document in documents} == {"1.0", "2.0"}


def test_importing_twice_does_not_duplicate_chunks(seeded_database: dict[str, str], fake_embeddings: None) -> None:
    first = import_product_docs()
    second = import_product_docs()
    with get_connection() as connection:
        count = connection.execute("SELECT COUNT(*) AS count FROM support.document_chunks").fetchone()["count"]
    assert first == {"found": 14, "imported": 14, "skipped": 0}
    assert second == {"found": 14, "imported": 0, "skipped": 14}
    assert count == 14


def test_company_b_cannot_retrieve_company_a_documents(seeded_database: dict[str, str], fake_embeddings: None) -> None:
    import_product_docs()
    auth_b = authenticate(seeded_database["token_b"])
    results = retrieve_documents("订单同步", auth_b, None)
    assert results == []


def test_tokenize_preserves_error_code() -> None:
    assert "order_sync_disabled" in tokenize("ORDER_SYNC_DISABLED 是什么意思？")


def test_rrf_rewards_chunk_found_by_both_routes() -> None:
    first = RetrievedChunk(chunk_id="1", title="A", source_uri="a", version="2.0", content="a", score=1)
    second = RetrievedChunk(chunk_id="2", title="B", source_uri="b", version="2.0", content="b", score=1)
    result = reciprocal_rank_fusion([[first, second], [second]])
    assert result[0].chunk_id == "2"


def test_expired_version_is_filtered_before_ranking(seeded_database: dict[str, str], fake_embeddings: None) -> None:
    import_product_docs()
    auth = authenticate(seeded_database["token_a"])
    results = retrieve_documents("旧版同步入口", auth, None, mode="hybrid")
    assert all(chunk.version == "2.0" for chunk in results)
    assert all("legacy-order-sync" not in chunk.source_uri for chunk in results)


def test_rerank_failure_records_fallback(seeded_database: dict[str, str], fake_embeddings: None, monkeypatch) -> None:
    import_product_docs()
    auth = authenticate(seeded_database["token_a"])

    def fail_rerank(*args, **kwargs):
        raise RuntimeError("test reranker outage")

    monkeypatch.setattr("app.docs.rerank_chunks", fail_rerank)
    results = retrieve_documents("订单同步", auth, None, mode="hybrid_rerank")
    assert results
    with get_connection() as connection:
        row = connection.execute("SELECT rerank_error FROM support.retrieval_runs ORDER BY created_at DESC LIMIT 1").fetchone()
    assert row["rerank_error"] == "RuntimeError"
