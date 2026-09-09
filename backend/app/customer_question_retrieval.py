from datetime import date

from langchain_core.tools import tool
from pydantic import BaseModel

from app.customer_knowledge_database import get_customer_knowledge_database_client


class CustomerQuestionRetrievalResult(BaseModel):
    chunk_id: str
    source_uri: str
    version: str
    content: str


@tool
def retrieve_documents_for_customer_question(customer_question: str, affected_feature: str, version: str) -> list[dict[str, object]]:
    """Retrieve current customer-visible documents related to a customer question."""

    current_date = date.today()

    document_filter = {
        "$and": [
            {"visibility": {"$eq": "CUSTOMER"}},
            {"feature": {"$eq": affected_feature}},
            {"version": {"$eq": version}},
            {"effective_from": {"$lte": current_date}},
            {
                "$or": [
                    {"effective_to": {"$exists": False}},
                    {"effective_to": {"$gte": current_date}},
                ]
            },
        ]
    }

    knowledge_database = get_customer_knowledge_database_client()
    retrieved_documents = knowledge_database.similarity_search(query=customer_question, k=3, filter=document_filter)

    results: list[dict[str, object]] = []

    for retrieved_document in retrieved_documents:
        result = CustomerQuestionRetrievalResult(
            chunk_id=str(retrieved_document.id),
            source_uri=str(retrieved_document.metadata["source_uri"]),
            version=str(retrieved_document.metadata["version"]),
            content=retrieved_document.page_content,
        )

        results.append(result.model_dump())

    return results