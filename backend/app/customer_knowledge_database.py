from langchain_huggingface import HuggingFaceEmbeddings
from langchain_postgres import Column, PGEngine, PGVectorStore

from app.core.config import settings


EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_VECTOR_SIZE = 384
CUSTOMER_KNOWLEDGE_TABLE_NAME = "customer_documents"

CUSTOMER_KNOWLEDGE_METADATA_COLUMNS = [
    Column("visibility", "VARCHAR", nullable=False),
    Column("feature", "VARCHAR", nullable=False),
    Column("version", "VARCHAR", nullable=False),
    Column("source_uri", "VARCHAR", nullable=False),
    Column("effective_from", "DATE", nullable=False),
    Column("effective_to", "DATE", nullable=True),
]

CUSTOMER_KNOWLEDGE_METADATA_FIELDS = [column.name for column in CUSTOMER_KNOWLEDGE_METADATA_COLUMNS]

embedding_model: HuggingFaceEmbeddings | None = None
knowledge_database_engine: PGEngine | None = None
knowledge_database_client: PGVectorStore | None = None


def get_embedding_model() -> HuggingFaceEmbeddings:
    global embedding_model

    if embedding_model is None:
        embedding_model = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL_NAME, encode_kwargs={"normalize_embeddings": True})

    return embedding_model


def get_customer_knowledge_database_engine() -> PGEngine:
    global knowledge_database_engine

    if knowledge_database_engine is None:
        knowledge_database_engine = PGEngine.from_connection_string(url=settings.database_url)

    return knowledge_database_engine


def reset_customer_knowledge_table() -> None:
    database_engine = get_customer_knowledge_database_engine()

    # Recreates the PostgreSQL table used to store customer document chunks and vectors.
    database_engine.init_vectorstore_table(
        table_name=CUSTOMER_KNOWLEDGE_TABLE_NAME,
        vector_size=EMBEDDING_VECTOR_SIZE,
        metadata_columns=CUSTOMER_KNOWLEDGE_METADATA_COLUMNS,
        id_column=Column("chunk_id", "VARCHAR", nullable=False),
        overwrite_existing=True,
    )


def get_customer_knowledge_database_client() -> PGVectorStore:
    global knowledge_database_client

    if knowledge_database_client is None:
        database_engine = get_customer_knowledge_database_engine()

        # Creates a Python object for reading and writing the existing customer knowledge table.
        knowledge_database_client = PGVectorStore.create_sync(
            engine=database_engine,
            table_name=CUSTOMER_KNOWLEDGE_TABLE_NAME,
            embedding_service=get_embedding_model(),
            metadata_columns=CUSTOMER_KNOWLEDGE_METADATA_FIELDS,
            id_column="chunk_id",
        )

    return knowledge_database_client