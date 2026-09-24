import json
import re
import time
from datetime import UTC, datetime
from functools import lru_cache
from uuid import uuid4

import jieba
import torch
from langsmith import traceable
from rank_bm25 import BM25Okapi
from transformers import AutoModelForCausalLM, AutoTokenizer

from backend.app import customer_document_ingestion
from backend.app.config import get_settings
from backend.app.database import get_connection
from backend.app.models import RetrievedChunk, UserContext
from backend.app.trace import current_trace_id

RETRIEVAL_MODES = {"vector_only", "hybrid", "hybrid_rerank"}


def filter_sql(user: UserContext, product: str | None, version: str | None) -> tuple[str, dict[str, object]]:
    clauses = ["c.company_id = %(company_id)s", "d.effective_from <= %(today)s", "(d.effective_to IS NULL OR d.effective_to >= %(today)s)"]
    parameters: dict[str, object] = {"company_id": user.company_id, "today": datetime.now(UTC).date()}
    if product:
        clauses.append("d.product = %(product)s")
        parameters["product"] = product
    if version:
        clauses.append("d.version = %(version)s")
        parameters["version"] = version
    return " AND ".join(clauses), parameters


def fetch_visible_chunks(user: UserContext, product: str | None = None, version: str | None = None) -> list[RetrievedChunk]:
    where, parameters = filter_sql(user, product, version)
    with get_connection() as connection:
        rows = connection.execute(f"""SELECT c.chunk_id::text, d.title, d.source_uri, d.version, c.content, 0.0::float AS score
            FROM support.document_chunks c JOIN support.product_documents d ON d.document_id = c.document_id WHERE {where}""", parameters).fetchall()
    return [RetrievedChunk(**row) for row in rows]


def vector_search(query: str, user: UserContext, product: str | None = None, version: str | None = None, limit: int = 15) -> list[RetrievedChunk]:
    query_vector = customer_document_ingestion.embed_texts([query], "RETRIEVAL_QUERY")[0]
    where, parameters = filter_sql(user, product, version)
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
def retrieve_customer_documents(query: str, user: UserContext, conversation_id: str | None, version: str | None = None, product: str | None = None, mode: str = "vector_only", limit: int = 5) -> list[RetrievedChunk]:
    if mode not in RETRIEVAL_MODES:
        raise ValueError(f"Unsupported retrieval mode: {mode}")
    started = time.perf_counter()
    vector_chunks = vector_search(query, user, product, version)
    keyword_chunks: list[RetrievedChunk] = []
    fused_chunks = vector_chunks
    reranked_chunks: list[RetrievedChunk] = []
    rerank_error: str | None = None
    rerank_latency_ms: int | None = None
    if mode != "vector_only":
        keyword_chunks = keyword_search(query, fetch_visible_chunks(user, product, version))
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
            (str(uuid4()), conversation_id, user.company_id, query, query, json.dumps(rank_ids(final_chunks)), latency_ms, current_trace_id(), mode, json.dumps({"company_id": user.company_id, "product": product, "version": version}), json.dumps(rank_ids(vector_chunks)), json.dumps(rank_ids(keyword_chunks)), json.dumps(rank_ids(fused_chunks)), json.dumps(rank_ids(reranked_chunks)), settings.rerank_model if mode == "hybrid_rerank" else None, rerank_latency_ms, rerank_error),
        )
    return final_chunks
