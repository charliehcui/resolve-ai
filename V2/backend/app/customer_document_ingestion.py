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

PRODUCT_DOCS_DIR = PROJECT_ROOT / "docs" / "product"


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
