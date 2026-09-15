from datetime import date
from pathlib import Path

import yaml
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.knowledge_database import get_knowledge_database_client, reset_knowledge_table


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CUSTOMER_DOCUMENTS_DIRECTORY = PROJECT_ROOT / "docs" / "customer"
INTERNAL_DOCUMENTS_DIRECTORY = PROJECT_ROOT / "docs" / "internal"

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100


def load_knowledge_document(document_path: Path, expected_visibility: str) -> Document:
    document_text = document_path.read_text(encoding="utf-8")
    document_parts = document_text.split("---", 2)

    if len(document_parts) != 3:
        raise ValueError(f"Document metadata is missing: {document_path}")

    metadata_text = document_parts[1].strip()
    document_content = document_parts[2].strip()
    metadata_values = yaml.safe_load(metadata_text)

    if isinstance(metadata_values, dict) is False:
        raise ValueError(f"Document metadata is invalid: {document_path}")

    required_fields = ["title", "visibility", "product", "feature", "version", "source_uri", "effective_from"]

    for required_field in required_fields:
        if metadata_values.get(required_field) is None:
            raise ValueError(f"Document field is missing: {required_field}")

    if metadata_values["visibility"] != expected_visibility:
        raise ValueError(f"Document must use {expected_visibility} visibility: {document_path}")

    effective_from = date.fromisoformat(str(metadata_values["effective_from"]))

    if metadata_values.get("effective_to") is not None:
        effective_to = date.fromisoformat(str(metadata_values["effective_to"]))
    else:
        effective_to = None

    metadata = {
        "title": str(metadata_values["title"]),
        "visibility": expected_visibility,
        "product": str(metadata_values["product"]),
        "feature": str(metadata_values["feature"]),
        "version": str(metadata_values["version"]),
        "source_uri": str(metadata_values["source_uri"]),
        "effective_from": effective_from,
        "effective_to": effective_to,
    }

    return Document(page_content=document_content, metadata=metadata)


def load_customer_document(document_path: Path) -> Document:
    return load_knowledge_document(document_path, "CUSTOMER")


def load_internal_document(document_path: Path) -> Document:
    return load_knowledge_document(document_path, "INTERNAL")


def load_all_knowledge_documents() -> list[Document]:
    knowledge_documents: list[Document] = []
    document_paths = [(path, "CUSTOMER") for path in sorted(CUSTOMER_DOCUMENTS_DIRECTORY.glob("*.md"))]
    document_paths.extend((path, "INTERNAL") for path in sorted(INTERNAL_DOCUMENTS_DIRECTORY.glob("*.md")))

    for document_path, visibility in document_paths:
        knowledge_document = load_knowledge_document(document_path, visibility)
        knowledge_documents.append(knowledge_document)

    return knowledge_documents


def create_chunk_ids(document_chunks: list[Document]) -> list[str]:
    chunk_numbers: dict[str, int] = {}
    chunk_ids: list[str] = []

    for document_chunk in document_chunks:
        source_uri = str(document_chunk.metadata["source_uri"])
        chunk_number = chunk_numbers.get(source_uri, 0)
        chunk_id = f"{source_uri}:{chunk_number}"

        chunk_ids.append(chunk_id)
        chunk_numbers[source_uri] = chunk_number + 1

    return chunk_ids


def import_knowledge_documents() -> int:
    knowledge_documents = load_all_knowledge_documents()

    if len(knowledge_documents) == 0:
        raise ValueError("No knowledge documents were found")

    text_splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    document_chunks = text_splitter.split_documents(knowledge_documents)
    chunk_ids = create_chunk_ids(document_chunks)

    reset_knowledge_table()

    knowledge_database = get_knowledge_database_client()
    knowledge_database.add_documents(documents=document_chunks, ids=chunk_ids)

    return len(document_chunks)


if __name__ == "__main__":
    imported_chunk_count = import_knowledge_documents()
    print(f"Imported {imported_chunk_count} knowledge document chunks.")
