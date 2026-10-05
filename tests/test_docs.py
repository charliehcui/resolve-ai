from collections import Counter
from datetime import date

import pytest
import yaml
from google.genai import errors

from backend.app import customer_document_ingestion as ingestion
from backend.app.auth import authenticate
from backend.app.customer_document_ingestion import CHUNK_SIZE, build_document_chunks, discover_product_documents, import_product_documents, load_product_documents
from backend.app.customer_retrieval import reciprocal_rank_fusion, retrieve_customer_documents, tokenize
from backend.app.database import get_connection
from backend.app.models import RetrievedChunk


def test_all_product_docs_have_required_metadata() -> None:
    documents = load_product_documents()
    assert len(documents) == 23
    assert Counter(path.suffix for path in discover_product_documents()) == {".md": 13, ".pdf": 3, ".docx": 4, ".xlsx": 3}
    assert all(document["company_id"] == "company-a" for document in documents)
    assert {document["version"] for document in documents} == {"1.0", "2.0"}
    required_fields = {"title", "company_id", "product", "version", "effective_from", "effective_to", "status", "last_reviewed", "topic", "source_uri"}
    for document in documents:
        assert required_fields.issubset(document)
        assert document["source_uri"] == document["path"].relative_to(ingestion.PROJECT_ROOT).as_posix()
        assert document["last_reviewed"] == date(2026, 10, 4)
        assert document["content"].strip()
        if document["version"] == "2.0":
            assert document["effective_from"] == date(2026, 1, 1)
            assert document["effective_to"] is None
            assert document["status"] == "当前"
        else:
            assert document["effective_to"] == date(2025, 12, 31)
            assert document["status"] == "历史资料"


def test_text_chunks_keep_context_and_have_bounded_lengths() -> None:
    for document in load_product_documents():
        if document["path"].suffix == ".xlsx":
            continue
        chunks = build_document_chunks(document)
        assert len(chunks) >= 2
        assert all(200 <= len(chunk["content"]) <= CHUNK_SIZE for chunk in chunks)
        assert all(document["title"] in chunk["content"] and document["topic"] in chunk["content"] for chunk in chunks)
        assert build_document_chunks(document) == chunks


def test_excel_chunks_preserve_complete_rows_and_overviews() -> None:
    counts = {"business-states.xlsx": 57, "channel-capabilities.xlsx": 12, "error-codes.xlsx": 48}
    for document in load_product_documents():
        if document["path"].suffix != ".xlsx":
            continue
        chunks = build_document_chunks(document)
        assert len(chunks) == counts[document["path"].name] + 1
        assert "业务规则" in chunks[0]["content"]
        for record, chunk in zip(document["records"][1:], chunks[1:], strict=True):
            assert record["content"] in chunk["content"]
            assert f"行：{record['row']}" in chunk["content"]
            for header in ("代码 / 状态", "对象 / 范围", "含义", "条件 / 必要证据", "用户下一步", "不能据此承诺", "代码来源"):
                assert f"{header}：" in chunk["content"]


def test_importing_twice_does_not_duplicate_chunks(seeded_database: dict[str, str], fake_embeddings: None) -> None:
    first = import_product_documents()
    second = import_product_documents()
    with get_connection() as connection:
        count = connection.execute("SELECT COUNT(*) AS count FROM support.document_chunks").fetchone()["count"]
        rows = connection.execute("""SELECT d.source_uri, d.company_id, d.version, COUNT(c.chunk_id) AS chunks FROM support.product_documents d
            JOIN support.document_chunks c ON c.document_id = d.document_id GROUP BY d.document_id""").fetchall()
        wrong_links = connection.execute("""SELECT COUNT(*) AS count FROM support.document_chunks c LEFT JOIN support.product_documents d ON d.document_id = c.document_id
            WHERE d.document_id IS NULL OR c.company_id <> d.company_id OR vector_dims(c.embedding) <> 1024""").fetchone()["count"]
    expected = {document["source_uri"]: len(build_document_chunks(document)) for document in load_product_documents()}
    assert first["found"] == first["imported"] == 23
    assert first["embedded"] == count == sum(expected.values())
    assert second["imported"] == second["embedded"] == second["removed_chunks"] == second["removed_documents"] == 0
    assert second["skipped"] == 23
    assert second["reused"] == count
    assert {row["source_uri"]: row["chunks"] for row in rows} == expected
    assert len(rows) == 23 and wrong_links == 0


def write_document(path, content, **overrides):
    metadata = {"title": "测试业务规则", "company_id": "company-a", "product": "merchant-console", "version": "2.0", "effective_from": date(2026, 1, 1), "effective_to": None, "status": "当前", "last_reviewed": date(2026, 10, 4), "topic": "Test / Rules"}
    metadata.update(overrides)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\n" + yaml.safe_dump(metadata, allow_unicode=True) + "---\n" + content, encoding="utf-8")


def use_test_sources(monkeypatch, tmp_path):
    directory = tmp_path / "docs" / "product"
    directory.mkdir(parents=True)
    monkeypatch.setattr(ingestion, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(ingestion, "PRODUCT_DOCUMENTS_DIR", directory)
    return directory


def test_metadata_only_change_updates_dates_without_embedding(seeded_database, fake_embeddings, monkeypatch, tmp_path):
    directory = use_test_sources(monkeypatch, tmp_path)
    path = directory / "rule.md"
    write_document(path, "A complete business rule.")
    import_product_documents()
    write_document(path, "A complete business rule.", effective_from=date(2027, 1, 1))
    result = import_product_documents()
    with get_connection() as connection:
        row = connection.execute("SELECT effective_from FROM support.product_documents").fetchone()
    assert row["effective_from"] == date(2027, 1, 1)
    assert result["embedded"] == 0 and result["reused"] == 1


def test_changed_document_replaces_all_old_chunks(seeded_database, fake_embeddings, monkeypatch, tmp_path):
    directory = use_test_sources(monkeypatch, tmp_path)
    path = directory / "nested" / "rule.md"
    write_document(path, "Original complete business rule.\n\n" * 100)
    import_product_documents()
    with get_connection() as connection:
        original = connection.execute("SELECT chunk_id, document_id FROM support.document_chunks").fetchall()
    assert len(original) > 1
    write_document(path, "Replacement rule with different conditions.")
    result = import_product_documents()
    with get_connection() as connection:
        current = connection.execute("SELECT chunk_id, document_id, content FROM support.document_chunks").fetchall()
    assert len(current) == 1 and result["removed_chunks"] == len(original)
    assert current[0]["document_id"] == original[0]["document_id"]
    assert current[0]["chunk_id"] not in {row["chunk_id"] for row in original}
    assert "Replacement rule" in current[0]["content"]


def test_migrated_and_deleted_sources_are_removed_only_within_product_root(seeded_database, fake_embeddings, monkeypatch, tmp_path):
    directory = use_test_sources(monkeypatch, tmp_path)
    path = directory / "old.md"
    deleted = directory / "removed.md"
    write_document(path, "Source to migrate.")
    write_document(deleted, "Source to delete.")
    import_product_documents()
    with get_connection() as connection:
        connection.execute("""INSERT INTO support.product_documents (document_id, company_id, title, source_uri, product, version, effective_from)
            VALUES ('00000000-0000-0000-0000-000000000001', 'company-a', 'Other source', 'docs/other/keep.md', 'merchant-console', '2.0', '2026-01-01')""")
    relocated = directory / "nested" / "new.md"
    relocated.parent.mkdir()
    path.rename(relocated)
    deleted.unlink()
    result = import_product_documents()
    with get_connection() as connection:
        sources = {row["source_uri"] for row in connection.execute("SELECT source_uri FROM support.product_documents").fetchall()}
        orphan_count = connection.execute("""SELECT COUNT(*) AS count FROM support.document_chunks c LEFT JOIN support.product_documents d ON d.document_id = c.document_id
            WHERE d.document_id IS NULL""").fetchone()["count"]
    assert sources == {"docs/product/nested/new.md", "docs/other/keep.md"}
    assert result["removed_documents"] == 2 and result["removed_chunks"] == 2
    assert orphan_count == 0


@pytest.mark.parametrize("failure", ["parse", "embedding", "empty_directory"])
def test_failed_import_keeps_previous_knowledge(seeded_database, fake_embeddings, monkeypatch, tmp_path, failure):
    directory = use_test_sources(monkeypatch, tmp_path)
    path = directory / "rule.md"
    write_document(path, "Previously indexed rule.")
    import_product_documents()
    with get_connection() as connection:
        before = connection.execute("SELECT chunk_id, content FROM support.document_chunks").fetchall()
    if failure == "parse":
        (directory / "invalid.md").write_text("Missing metadata", encoding="utf-8")
    elif failure == "empty_directory":
        path.unlink()
    else:
        write_document(path, "Changed rule requiring another embedding.")
        def unavailable(*args):
            raise RuntimeError("Embedding unavailable")
        monkeypatch.setattr(ingestion, "generate_text_embeddings", unavailable)
    with pytest.raises((ValueError, RuntimeError)):
        import_product_documents()
    with get_connection() as connection:
        after = connection.execute("SELECT chunk_id, content FROM support.document_chunks").fetchall()
    assert after == before


def test_identical_text_in_different_sources_keeps_both_documents(seeded_database, fake_embeddings, monkeypatch, tmp_path):
    directory = use_test_sources(monkeypatch, tmp_path)
    write_document(directory / "one.md", "Identical business rule.")
    write_document(directory / "two.md", "Identical business rule.")
    result = import_product_documents()
    with get_connection() as connection:
        row = connection.execute("SELECT COUNT(*) AS count, COUNT(DISTINCT document_id) AS documents FROM support.document_chunks").fetchone()
    assert row == {"count": 2, "documents": 2} and result["embedded"] == 2


@pytest.mark.parametrize("persistent", [False, True])
def test_embedding_rate_limit_has_bounded_batch_retries(seeded_database, fake_embeddings, monkeypatch, tmp_path, persistent):
    directory = use_test_sources(monkeypatch, tmp_path)
    write_document(directory / "rule.md", "A complete business rule.")
    embed = ingestion.generate_text_embeddings
    calls = []
    delays = []
    def rate_limited(texts, task_type):
        calls.append(task_type)
        if persistent or len(calls) == 1:
            raise errors.ClientError(429, {"error": {"details": [{"@type": "google.rpc.RetryInfo", "retryDelay": "9s"}]}})
        return embed(texts, task_type)
    monkeypatch.setattr(ingestion, "generate_text_embeddings", rate_limited)
    monkeypatch.setattr(ingestion.time, "sleep", delays.append)
    if persistent:
        with pytest.raises(errors.ClientError):
            import_product_documents()
        with get_connection() as connection:
            assert connection.execute("SELECT COUNT(*) AS count FROM support.product_documents").fetchone()["count"] == 0
        assert len(calls) == 3 and delays == [10, 10]
    else:
        assert import_product_documents()["embedded"] == 1
        assert len(calls) == 2 and delays == [10]


def test_company_b_cannot_retrieve_company_a_documents(seeded_database: dict[str, str], fake_embeddings: None) -> None:
    import_product_documents()
    auth_b = authenticate(seeded_database["token_b"])
    results = retrieve_customer_documents("订单同步", auth_b, None)
    assert results == []


def test_tokenize_preserves_error_code() -> None:
    assert "order_sync_disabled" in tokenize("ORDER_SYNC_DISABLED 是什么意思？")


def test_rrf_rewards_chunk_found_by_both_routes() -> None:
    first = RetrievedChunk(chunk_id="1", title="A", source_uri="a", version="2.0", content="a", score=1)
    second = RetrievedChunk(chunk_id="2", title="B", source_uri="b", version="2.0", content="b", score=1)
    result = reciprocal_rank_fusion([[first, second], [second]])
    assert result[0].chunk_id == "2"


def test_expired_version_is_filtered_before_ranking(seeded_database: dict[str, str], fake_embeddings: None) -> None:
    import_product_documents()
    user = authenticate(seeded_database["token_a"])
    results = retrieve_customer_documents("旧版同步入口", user, None, mode="hybrid")
    assert all(chunk.version == "2.0" for chunk in results)
    assert all("legacy-order-sync" not in chunk.source_uri for chunk in results)


def test_rerank_failure_records_fallback(seeded_database: dict[str, str], fake_embeddings: None, monkeypatch) -> None:
    import_product_documents()
    user = authenticate(seeded_database["token_a"])

    def fail_rerank(*args, **kwargs):
        raise RuntimeError("test reranker outage")

    monkeypatch.setattr("backend.app.customer_retrieval.rerank_chunks", fail_rerank)
    results = retrieve_customer_documents("订单同步", user, None, mode="hybrid_rerank")
    assert results
    with get_connection() as connection:
        row = connection.execute("SELECT rerank_error FROM support.retrieval_runs ORDER BY created_at DESC LIMIT 1").fetchone()
    assert row["rerank_error"] == "RuntimeError"
