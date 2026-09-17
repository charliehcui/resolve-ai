import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"
load_dotenv(ENV_FILE, override=False)


def require_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value.strip()


def psycopg_url(database_url: str) -> str:
    normalized_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    return normalized_url.replace("@localhost:", "@127.0.0.1:", 1)


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    groq_api_key: str = field(repr=False)
    groq_model: str
    google_api_key: str = field(repr=False)
    google_model: str
    google_fallback_model: str
    google_embedding_model: str
    embedding_dimension: int
    rerank_model: str
    retrieval_mode: str
    langsmith_tracing: bool
    langsmith_project: str

    @property
    def postgres_url(self) -> str:
        return psycopg_url(self.database_url)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    dimension = int(require_env("EMBEDDING_DIMENSION"))
    if dimension != 1024:
        raise RuntimeError("EMBEDDING_DIMENSION must be 1024 for db/001_support.sql")
    retrieval_mode = os.getenv("RETRIEVAL_MODE", "vector_only")
    if retrieval_mode not in {"vector_only", "hybrid", "hybrid_rerank"}:
        raise RuntimeError("RETRIEVAL_MODE must be vector_only, hybrid, or hybrid_rerank")
    return Settings(
        database_url=require_env("DATABASE_URL"),
        groq_api_key=require_env("GROQ_API_KEY"),
        groq_model=require_env("GROQ_MODEL"),
        google_api_key=require_env("GOOGLE_API_KEY"),
        google_model=require_env("GOOGLE_MODEL"),
        google_fallback_model=require_env("GOOGLE_FALLBACK_MODEL"),
        google_embedding_model=require_env("GOOGLE_EMBEDDING_MODEL"),
        embedding_dimension=dimension,
        rerank_model=require_env("RERANK_MODEL"),
        retrieval_mode=retrieval_mode,
        langsmith_tracing=os.getenv("LANGSMITH_TRACING", "false").lower() == "true",
        langsmith_project=os.getenv("LANGSMITH_PROJECT", "resolveai-v2"),
    )
