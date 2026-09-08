from datetime import date

from langchain_core.tools import tool
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer
from sqlalchemy import or_, select

from app.db.database import SessionLocal
from app.db.models import Document, DocumentChunk

EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

embedding_model: SentenceTransformer | None = None


class CustomerQuestionRetrievalResult(BaseModel):
    chunk_id: int
    source_uri: str
    version: str
    content: str
    score: float


def load_embedding_model() -> SentenceTransformer:
    global embedding_model

    if embedding_model is None:
        embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)

    return embedding_model


def convert_text_to_vector(text: str) -> list[float]:
    model = load_embedding_model()
    embedding = model.encode(text, normalize_embeddings=True)
    return embedding.tolist()


@tool
def retrieve_documents_for_customer_question(customer_question: str, feature: str, version: str) -> list[dict[str, object]]:
    """Retrieve current customer-visible documents related to a customer question."""

    question_vector = convert_text_to_vector(customer_question)
    current_date = date.today()
    distance = DocumentChunk.embedding.cosine_distance(question_vector).label("distance")

    statement = select(DocumentChunk.id.label("chunk_id"), Document.source_uri, Document.version, DocumentChunk.content, distance)
    statement = statement.join(Document, Document.id == DocumentChunk.document_id)
    statement = statement.where(Document.visibility == "CUSTOMER")
    statement = statement.where(Document.feature == feature)
    statement = statement.where(Document.version == version)
    statement = statement.where(Document.effective_from <= current_date)
    statement = statement.where(or_(Document.effective_to.is_(None), Document.effective_to >= current_date))
    statement = statement.order_by(distance)
    statement = statement.limit(3)

    retrieved_documents = []

    with SessionLocal() as database:
        rows = database.execute(statement).all()

        for row in rows:
            result = CustomerQuestionRetrievalResult(
                chunk_id=row.chunk_id,
                source_uri=row.source_uri,
                version=row.version,
                content=row.content,
                score=round(1.0 - float(row.distance), 4),
            )
            retrieved_documents.append(result.model_dump())

    return retrieved_documents
