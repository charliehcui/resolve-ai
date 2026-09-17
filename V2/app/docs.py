import hashlib
import json
import math
import re
import time
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import jieba
import torch
import yaml
from google import genai
from google.genai import types
from langsmith import traceable
from rank_bm25 import BM25Okapi
from transformers import AutoModelForCausalLM, AutoTokenizer

from app.config import PROJECT_ROOT, get_settings
from app.db import get_connection
from app.models import AuthContext, RetrievedChunk
from app.trace import current_trace_id

PRODUCT_DOCS_DIR = PROJECT_ROOT / "docs" / "product"
RETRIEVAL_MODES = {"vector_only", "hybrid", "hybrid_rerank"}


def normalize_vector(values: list[float]) -> list[float]:
    length = math.sqrt(sum(value * value for value in values))
    if length == 0:
        raise RuntimeError("Embedding service returned a zero vector")
    return [value / length for value in values]


@traceable(name="google_embedding", run_type="embedding")
def embed_texts(texts: list[str], task_type: str) -> list[list[float]]:
    settings = get_settings()
    client = genai.Client(api_key=settings.google_api_key)
    result = client.models.embed_content(model=settings.google_embedding_model, contents=texts, config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=settings.embedding_dimension))
    client.close()
    vectors = [normalize_vector(list(item.values or [])) for item in result.embeddings or []]
    if len(vectors) != len(texts) or any(len(vector) != settings.embedding_dimension for vector in vectors):
        raise RuntimeError("Embedding response did not match the requested count or dimension")
    return vectors


def parse_product_doc(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"Missing YAML front matter: {path}")
    _, raw_metadata, content = text.split("---\n", 2)
    metadata = yaml.safe_load(raw_metadata)
    required = {"title", "company_id", "product", "version", "effective_from"}
    missing = required.difference(metadata)
    if missing:
        raise ValueError(f"Missing metadata {sorted(missing)}: {path}")
    return {"path": path, "content": content.strip(), **metadata}


@traceable(name="import_customer_documents", run_type="chain")
def import_product_docs() -> dict[str, int]:
    documents = [parse_product_doc(path) for path in sorted(PRODUCT_DOCS_DIR.glob("*.md"))]
    with get_connection() as connection:
        existing_rows = connection.execute("SELECT company_id, content_hash FROM support.document_chunks").fetchall()
    existing_hashes = {(row["company_id"], row["content_hash"]) for row in existing_rows}
    pending: list[dict[str, object]] = []
    for document in documents:
        source_uri = Path(document["path"]).relative_to(PROJECT_ROOT).as_posix()
        content_hash = hashlib.sha256(f"{document['company_id']}|{document['version']}|{document['content']}".encode()).hexdigest()
        if (document["company_id"], content_hash) not in existing_hashes:
            pending.append({**document, "source_uri": source_uri, "content_hash": content_hash})
    vectors = embed_texts([str(document["content"]) for document in pending], "RETRIEVAL_DOCUMENT") if pending else []
    imported = 0
    with get_connection() as connection:
        for document, vector in zip(pending, vectors, strict=True):
            document_key = f"{document['company_id']}:{document['source_uri']}:{document['version']}"
            document_id = str(uuid5(NAMESPACE_URL, document_key))
            chunk_id = str(uuid5(NAMESPACE_URL, f"{document_key}:{document['content_hash']}"))
            connection.execute(
                """INSERT INTO support.product_documents (document_id, company_id, title, source_uri, product, version, effective_from, effective_to)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (company_id, source_uri, version) DO UPDATE SET title = EXCLUDED.title, product = EXCLUDED.product, effective_from = EXCLUDED.effective_from, effective_to = EXCLUDED.effective_to""",
                (document_id, document["company_id"], document["title"], document["source_uri"], document["product"], str(document["version"]), document["effective_from"], document.get("effective_to")),
            )
            connection.execute("DELETE FROM support.document_chunks WHERE document_id = %s AND content_hash <> %s", (document_id, document["content_hash"]))
            cursor = connection.execute(
                """INSERT INTO support.document_chunks (chunk_id, document_id, company_id, content, content_hash, embedding)
                VALUES (%s, %s, %s, %s, %s, %s::vector) ON CONFLICT (company_id, content_hash) DO NOTHING""",
                (chunk_id, document_id, document["company_id"], document["content"], document["content_hash"], json.dumps(vector)),
            )
            imported += cursor.rowcount
    return {"found": len(documents), "imported": imported, "skipped": len(documents) - imported}


def filter_sql(auth: AuthContext, product: str | None, version: str | None) -> tuple[str, dict[str, object]]:
    clauses = ["c.company_id = %(company_id)s", "d.effective_from <= %(today)s", "(d.effective_to IS NULL OR d.effective_to >= %(today)s)"]
    parameters: dict[str, object] = {"company_id": auth.company_id, "today": datetime.now(UTC).date()}
    if product:
        clauses.append("d.product = %(product)s")
        parameters["product"] = product
    if version:
        clauses.append("d.version = %(version)s")
        parameters["version"] = version
    return " AND ".join(clauses), parameters


def fetch_visible_chunks(auth: AuthContext, product: str | None = None, version: str | None = None) -> list[RetrievedChunk]:
    where, parameters = filter_sql(auth, product, version)
    with get_connection() as connection:
        rows = connection.execute(f"""SELECT c.chunk_id::text, d.title, d.source_uri, d.version, c.content, 0.0::float AS score
            FROM support.document_chunks c JOIN support.product_documents d ON d.document_id = c.document_id WHERE {where}""", parameters).fetchall()
    return [RetrievedChunk(**row) for row in rows]


def vector_search(query: str, auth: AuthContext, product: str | None = None, version: str | None = None, limit: int = 15) -> list[RetrievedChunk]:
    query_vector = embed_texts([query], "RETRIEVAL_QUERY")[0]
    where, parameters = filter_sql(auth, product, version)
    parameters.update({"query_vector": json.dumps(query_vector), "limit": limit})
    with get_connection() as connection:
        rows = connection.execute(f"""SELECT c.chunk_id::text, d.title, d.source_uri, d.version, c.content, 1 - (c.embedding <=> %(query_vector)s::vector) AS score
            FROM support.document_chunks c JOIN support.product_documents d ON d.document_id = c.document_id WHERE {where}
            ORDER BY c.embedding <=> %(query_vector)s::vector LIMIT %(limit)s""", parameters).fetchall()
    return [RetrievedChunk(**row) for row in rows]


def tokenize(text: str) -> list[str]:
    lowered = text.lower()
    protected = re.findall(r"[a-z0-9]+(?:[_./:-][a-z0-9]+)+", lowered)
    words = [token.strip() for token in jieba.lcut(lowered) if token.strip()]
    return protected + words


def keyword_search(query: str, chunks: list[RetrievedChunk], limit: int = 15) -> list[RetrievedChunk]:
    if not chunks:
        return []
    scores = BM25Okapi([tokenize(f"{chunk.title} {chunk.content}") for chunk in chunks]).get_scores(tokenize(query))
    ranked = sorted(zip(chunks, scores, strict=True), key=lambda item: float(item[1]), reverse=True)
    return [chunk.model_copy(update={"score": float(score)}) for chunk, score in ranked[:limit] if float(score) > 0]


def reciprocal_rank_fusion(rankings: list[list[RetrievedChunk]], rank_constant: int = 60, limit: int = 20) -> list[RetrievedChunk]:
    scores: dict[str, float] = {}
    chunks: dict[str, RetrievedChunk] = {}
    for ranking in rankings:
        for rank, chunk in enumerate(ranking, start=1):
            chunks[chunk.chunk_id] = chunk
            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1 / (rank_constant + rank)
    ordered_ids = sorted(scores, key=scores.get, reverse=True)[:limit]
    return [chunks[chunk_id].model_copy(update={"score": scores[chunk_id]}) for chunk_id in ordered_ids]


@lru_cache(maxsize=1)
def load_reranker() -> tuple[object, object, int, int]:
    model_name = get_settings().rerank_model
    tokenizer = AutoTokenizer.from_pretrained(model_name, padding_side="left")
    model = AutoModelForCausalLM.from_pretrained(model_name).eval()
    return tokenizer, model, tokenizer.convert_tokens_to_ids("no"), tokenizer.convert_tokens_to_ids("yes")


@traceable(name="qwen3_rerank", run_type="llm")
def rerank_chunks(query: str, chunks: list[RetrievedChunk], limit: int = 5) -> list[RetrievedChunk]:
    if not chunks:
        return []
    tokenizer, model, false_token_id, true_token_id = load_reranker()
    prefix = '<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct provided. The answer can only be "yes" or "no".<|im_end|>\n<|im_start|>user\n'
    suffix = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'
    instruction = "Retrieve product documentation that directly answers the merchant's question."
    pairs = [f"{prefix}<Instruct>: {instruction}\n<Query>: {query}\n<Document>: {chunk.title}\n{chunk.content}{suffix}" for chunk in chunks]
    inputs = tokenizer(pairs, padding=True, truncation=True, max_length=4096, return_tensors="pt")
    with torch.no_grad():
        logits = model(**inputs).logits[:, -1, :]
        pair_logits = torch.stack([logits[:, false_token_id], logits[:, true_token_id]], dim=1)
        scores = torch.nn.functional.log_softmax(pair_logits, dim=1)[:, 1].exp().tolist()
    ranked = sorted(zip(chunks, scores, strict=True), key=lambda item: item[1], reverse=True)
    return [chunk.model_copy(update={"score": float(score)}) for chunk, score in ranked[:limit]]


def rank_ids(chunks: list[RetrievedChunk]) -> list[str]:
    return [chunk.chunk_id for chunk in chunks]


@traceable(name="customer_hybrid_retrieval", run_type="retriever")
def retrieve_documents(query: str, auth: AuthContext, conversation_id: str | None, version: str | None = None, product: str | None = None, mode: str = "vector_only", limit: int = 5) -> list[RetrievedChunk]:
    if mode not in RETRIEVAL_MODES:
        raise ValueError(f"Unsupported retrieval mode: {mode}")
    started = time.perf_counter()
    vector_chunks = vector_search(query, auth, product, version)
    keyword_chunks: list[RetrievedChunk] = []
    fused_chunks = vector_chunks
    reranked_chunks: list[RetrievedChunk] = []
    rerank_error: str | None = None
    rerank_latency_ms: int | None = None
    if mode != "vector_only":
        keyword_chunks = keyword_search(query, fetch_visible_chunks(auth, product, version))
        fused_chunks = reciprocal_rank_fusion([vector_chunks, keyword_chunks])
    final_chunks = fused_chunks[:limit]
    if mode == "hybrid_rerank":
        rerank_started = time.perf_counter()
        try:
            reranked_chunks = rerank_chunks(query, fused_chunks, limit)
            final_chunks = reranked_chunks
        except Exception as error:  # noqa: BLE001 - any reranker failure must use the recorded hybrid fallback
            rerank_error = type(error).__name__
            final_chunks = fused_chunks[:limit]
        rerank_latency_ms = int((time.perf_counter() - rerank_started) * 1000)
    latency_ms = int((time.perf_counter() - started) * 1000)
    settings = get_settings()
    with get_connection() as connection:
        connection.execute(
            """INSERT INTO support.retrieval_runs (retrieval_id, conversation_id, company_id, query, search_query, chunk_ids, latency_ms, trace_id, mode, filters, vector_ranks, keyword_ranks, fused_ranks, rerank_ranks, rerank_model, rerank_latency_ms, rerank_error)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s)""",
            (str(uuid4()), conversation_id, auth.company_id, query, query, json.dumps(rank_ids(final_chunks)), latency_ms, current_trace_id(), mode, json.dumps({"company_id": auth.company_id, "product": product, "version": version}), json.dumps(rank_ids(vector_chunks)), json.dumps(rank_ids(keyword_chunks)), json.dumps(rank_ids(fused_chunks)), json.dumps(rank_ids(reranked_chunks)), settings.rerank_model if mode == "hybrid_rerank" else None, rerank_latency_ms, rerank_error),
        )
    return final_chunks
