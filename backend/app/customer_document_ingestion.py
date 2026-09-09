from datetime import date
from pathlib import Path

import yaml
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.customer_knowledge_database import get_customer_knowledge_database_client, reset_customer_knowledge_table


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CUSTOMER_DOCUMENTS_DIRECTORY = PROJECT_ROOT / "docs" / "customer"

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100


def load_customer_document(document_path: Path) -> Document:
    document_text = document_path.read_text(encoding="utf-8")
    document_parts = document_text.split("---", 2)

    if len(document_parts) != 3:
        raise ValueError(f"Document metadata is missing: {document_path}")

    metadata_text = document_parts[1].strip()
    document_content = document_parts[2].strip()
    metadata_values = yaml.safe_load(metadata_text)

    if not isinstance(metadata_values, dict):
        raise ValueError(f"Document metadata is invalid: {document_path}")

    required_fields = ["title", "visibility", "feature", "version", "source_uri", "effective_from"]

    for required_field in required_fields:
        if not metadata_values.get(required_field):
            raise ValueError(f"Document field is missing: {required_field}")

    if metadata_values["visibility"] != "CUSTOMER":
        raise ValueError(f"Customer document must use CUSTOMER visibility: {document_path}")

    effective_from = date.fromisoformat(str(metadata_values["effective_from"]))

    if metadata_values.get("effective_to"):
        effective_to = date.fromisoformat(str(metadata_values["effective_to"]))
    else:
        effective_to = None

    metadata = {
        "title": str(metadata_values["title"]),
        "visibility": "CUSTOMER",
        "feature": str(metadata_values["feature"]),
        "version": str(metadata_values["version"]),
        "source_uri": str(metadata_values["source_uri"]),
        "effective_from": effective_from,
        "effective_to": effective_to,
    }

    return Document(page_content=document_content, metadata=metadata)


def load_all_customer_documents() -> list[Document]:
    customer_documents: list[Document] = []
    document_paths = sorted(CUSTOMER_DOCUMENTS_DIRECTORY.glob("*.md"))

    for document_path in document_paths:
        customer_document = load_customer_document(document_path)
        customer_documents.append(customer_document)

    return customer_documents


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


def import_customer_documents() -> int:
    customer_documents = load_all_customer_documents()

    if not customer_documents:
        raise ValueError("No customer documents were found")

    text_splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    document_chunks = text_splitter.split_documents(customer_documents)
    chunk_ids = create_chunk_ids(document_chunks)

    reset_customer_knowledge_table()

    knowledge_database = get_customer_knowledge_database_client()
    knowledge_database.add_documents(documents=document_chunks, ids=chunk_ids)

    return len(document_chunks)


if __name__ == "__main__":
    imported_chunk_count = import_customer_documents()
    print(f"Imported {imported_chunk_count} customer document chunks.")