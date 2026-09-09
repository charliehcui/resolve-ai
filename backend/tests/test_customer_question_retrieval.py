from datetime import date

import pytest
from langchain_core.documents import Document

from app import customer_question_retrieval


class FakeCustomerDocumentDatabase:
    def __init__(self) -> None:
        self.search_filter: dict[str, object] = {}
        self.search_query = ""
        self.result_count = 0

    def similarity_search(self, query: str, k: int, filter: dict[str, object]) -> list[Document]:
        self.search_query = query
        self.result_count = k
        self.search_filter = filter
        return [Document(id="docs/customer/FAQ.md:0", page_content="Check the saved account credentials.", metadata={"source_uri": "docs/customer/FAQ.md", "version": "2026.8"})]


def test_customer_document_search_does_not_filter_by_feature(monkeypatch: pytest.MonkeyPatch) -> None:
    customer_document_database = FakeCustomerDocumentDatabase()
    monkeypatch.setattr(customer_question_retrieval, "get_customer_knowledge_database_client", lambda: customer_document_database)

    results = customer_question_retrieval.retrieve_documents_for_customer_question.invoke({"customer_question": "The order notification returns HTTP 401.", "version": "2026.8"})
    filter_rules = customer_document_database.search_filter["$and"]

    assert customer_document_database.search_query == "The order notification returns HTTP 401."
    assert customer_document_database.result_count == 3
    assert {"visibility": {"$eq": "CUSTOMER"}} in filter_rules
    assert {"version": {"$eq": "2026.8"}} in filter_rules
    assert {"effective_from": {"$lte": date.today()}} in filter_rules
    assert {"$or": [{"effective_to": {"$exists": False}}, {"effective_to": {"$gte": date.today()}}]} in filter_rules
    assert "feature" not in str(customer_document_database.search_filter)
    assert results == [{"chunk_id": "docs/customer/FAQ.md:0", "source_uri": "docs/customer/FAQ.md", "version": "2026.8", "content": "Check the saved account credentials."}]


def test_customer_document_search_skips_version_filter_when_version_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    customer_document_database = FakeCustomerDocumentDatabase()
    monkeypatch.setattr(customer_question_retrieval, "get_customer_knowledge_database_client", lambda: customer_document_database)

    customer_question_retrieval.retrieve_documents_for_customer_question.invoke({"customer_question": "The order notification does not arrive.", "version": None})

    assert "version" not in str(customer_document_database.search_filter)
    assert "affected_feature" not in customer_question_retrieval.retrieve_documents_for_customer_question.args_schema.model_fields
