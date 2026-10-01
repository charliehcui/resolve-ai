import hashlib
import json
import math
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import yaml
from google import genai
from google.genai import types
from langsmith import traceable

from backend.app.config import PROJECT_ROOT, get_settings
from backend.app.database import get_connection


PRODUCT_DOCUMENTS_DIR = PROJECT_ROOT / "docs" / "product"


def normalize_embedding_vector(values: list[float]) -> list[float]:
    squared_values: list[float] = []

    for value in values:
        squared_values.append(math.pow(value, 2))

    length = math.sqrt(sum(squared_values))

    if length == 0:
        raise RuntimeError("Embedding service returned a zero vector")

    normalized_values: list[float] = []

    for value in values:
        normalized_values.append(value / length)

    return normalized_values


@traceable(name="google_embedding", run_type="embedding")
def generate_text_embeddings(texts: list[str], task_type: str) -> list[list[float]]:
    settings = get_settings()
    client = genai.Client(api_key=settings.google_api_key)

    result = client.models.embed_content(
        model=settings.google_embedding_model,
        contents=texts,
        config=types.EmbedContentConfig(
            task_type=task_type,
            output_dimensionality=settings.embedding_dimension,
        ),
    )

    client.close()

    embedding_items = result.embeddings

    if embedding_items is None:
        embedding_items = []

    embedding_vectors: list[list[float]] = []

    for item in embedding_items:
        item_values = item.values

        if item_values is None:
            values: list[float] = []
        else:
            values = list(item_values)

        normalized_vector = normalize_embedding_vector(values)
        embedding_vectors.append(normalized_vector)

    if len(embedding_vectors) != len(texts):
        raise RuntimeError(
            "Embedding response did not match the requested count or dimension"
        )

    for embedding_vector in embedding_vectors:
        if len(embedding_vector) != settings.embedding_dimension:
            raise RuntimeError(
                "Embedding response did not match the requested count or dimension"
            )

    return embedding_vectors


def parse_product_document(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")

    if text.startswith("---\n") is False:
        raise ValueError(f"Missing YAML front matter: {path}")

    front_matter_parts = text.split("---\n", 2)

    if len(front_matter_parts) != 3:
        raise ValueError(f"Invalid YAML front matter: {path}")

    raw_metadata = front_matter_parts[1]
    content = front_matter_parts[2]

    loaded_metadata = yaml.safe_load(raw_metadata)

    if isinstance(loaded_metadata, dict) is False:
        raise ValueError(f"Invalid YAML metadata: {path}")

    metadata: dict[str, object] = dict(loaded_metadata)

    required_fields = {
        "title",
        "company_id",
        "product",
        "version",
        "effective_from",
    }

    missing_fields = required_fields.difference(metadata)

    if len(missing_fields) > 0:
        raise ValueError(
            f"Missing metadata {sorted(missing_fields)}: {path}"
        )

    document = dict(metadata)
    document["path"] = path
    document["content"] = content.strip()

    return document


@traceable(name="import_product_documents", run_type="chain")
def import_product_documents() -> dict[str, int]:
    documents: list[dict[str, object]] = []

    document_paths = sorted(PRODUCT_DOCUMENTS_DIR.glob("*.md"))

    for path in document_paths:
        document = parse_product_document(path)
        documents.append(document)

    with get_connection() as connection:
        existing_rows = connection.execute(
            """
            SELECT company_id, content_hash
            FROM support.document_chunks
            """
        ).fetchall()

    existing_document_hashes: set[tuple[object, object]] = set()

    for row in existing_rows:
        hash_key = (
            row["company_id"],
            row["content_hash"],
        )

        existing_document_hashes.add(hash_key)

    pending_documents: list[dict[str, object]] = []

    for document in documents:
        document_path = document["path"]

        if isinstance(document_path, Path) is False:
            raise ValueError("Product document path is invalid")

        source_uri = document_path.relative_to(PROJECT_ROOT).as_posix()

        hash_text = (
            f"{document['company_id']}|"
            f"{document['version']}|"
            f"{document['content']}"
        )

        content_hash = hashlib.sha256(
            hash_text.encode()
        ).hexdigest()

        document_hash_key = (
            document["company_id"],
            content_hash,
        )

        if document_hash_key in existing_document_hashes:
            continue

        pending_document = dict(document)
        pending_document["source_uri"] = source_uri
        pending_document["content_hash"] = content_hash

        pending_documents.append(pending_document)

    document_texts: list[str] = []

    for document in pending_documents:
        document_texts.append(str(document["content"]))

    embedding_vectors: list[list[float]] = []

    if len(document_texts) > 0:
        embedding_vectors = generate_text_embeddings(
            document_texts,
            "RETRIEVAL_DOCUMENT",
        )

    imported_count = 0

    with get_connection() as connection:
        for document, embedding_vector in zip(
            pending_documents,
            embedding_vectors,
            strict=True,
        ):
            document_key = (
                f"{document['company_id']}:"
                f"{document['source_uri']}:"
                f"{document['version']}"
            )

            document_id = str(
                uuid5(
                    NAMESPACE_URL,
                    document_key,
                )
            )

            chunk_key = (
                f"{document_key}:"
                f"{document['content_hash']}"
            )

            chunk_id = str(
                uuid5(
                    NAMESPACE_URL,
                    chunk_key,
                )
            )

            connection.execute(
                """
                INSERT INTO support.product_documents (
                    document_id,
                    company_id,
                    title,
                    source_uri,
                    product,
                    version,
                    effective_from,
                    effective_to
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                ON CONFLICT (
                    company_id,
                    source_uri,
                    version
                )
                DO UPDATE SET
                    title = EXCLUDED.title,
                    product = EXCLUDED.product,
                    effective_from = EXCLUDED.effective_from,
                    effective_to = EXCLUDED.effective_to
                """,
                (
                    document_id,
                    document["company_id"],
                    document["title"],
                    document["source_uri"],
                    document["product"],
                    str(document["version"]),
                    document["effective_from"],
                    document.get("effective_to"),
                ),
            )

            connection.execute(
                """
                DELETE FROM support.document_chunks
                WHERE document_id = %s
                AND content_hash <> %s
                """,
                (
                    document_id,
                    document["content_hash"],
                ),
            )

            cursor = connection.execute(
                """
                INSERT INTO support.document_chunks (
                    chunk_id,
                    document_id,
                    company_id,
                    content,
                    content_hash,
                    embedding
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s::vector
                )
                ON CONFLICT (
                    company_id,
                    content_hash
                )
                DO NOTHING
                """,
                (
                    chunk_id,
                    document_id,
                    document["company_id"],
                    document["content"],
                    document["content_hash"],
                    json.dumps(embedding_vector),
                ),
            )

            imported_count += cursor.rowcount

    skipped_count = len(documents) - imported_count

    return {
        "found": len(documents),
        "imported": imported_count,
        "skipped": skipped_count,
    }




# Product Markdown
# ↓
# parse_product_document()
# ↓
# metadata + 整篇正文
# ↓
# 检查 content_hash
# ↓
# generate_text_embeddings()
# ↓
# 1024 维 Document Embedding
# ↓
# normalize
# ↓
# PostgreSQL / pgvector


# Document Loading / Splitting
# → 可以用 LangChain

# Embedding
# → 可以直接用官方 SDK

# Storage
# → 自己 PostgreSQL + pgvector

# Retrieval
# → 自己控制 SQL / Hybrid / Rerank

# Evaluation
# → 自己统一测试