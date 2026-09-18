import os
import secrets
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["GROQ_API_KEY"] = "test-only"
os.environ["GROQ_MODEL"] = "groq-test-only"
os.environ["GOOGLE_API_KEY"] = "test-only"
os.environ["GOOGLE_MODEL"] = "google-primary-test-only"
os.environ["GOOGLE_FALLBACK_MODEL"] = "google-fallback-test-only"
os.environ["GOOGLE_EMBEDDING_MODEL"] = "embedding-test-only"
os.environ["EMBEDDING_DIMENSION"] = "1024"
os.environ["RERANK_MODEL"] = "rerank-test-only"
os.environ["RETRIEVAL_MODE"] = "vector_only"

from backend.app.auth import hash_token
from backend.app.config import get_settings, psycopg_url
from backend.app.database import get_connection, initialize_database


def replace_database_name(database_url: str, database_name: str) -> str:
    parts = urlsplit(psycopg_url(database_url))
    return urlunsplit((parts.scheme, parts.netloc, f"/{database_name}", parts.query, parts.fragment))


@pytest.fixture(scope="session", autouse=True)
def isolated_test_database() -> None:
    original_database_url = os.environ["DATABASE_URL"]
    admin_url = replace_database_name(original_database_url, "postgres")
    test_url = replace_database_name(original_database_url, "resolveai_v2_test")
    try:
        with psycopg.connect(admin_url, autocommit=True, connect_timeout=3) as connection:
            connection.execute("DROP DATABASE IF EXISTS resolveai_v2_test WITH (FORCE)")
            connection.execute("CREATE DATABASE resolveai_v2_test")
    except psycopg.Error:
        pytest.fail("The isolated PostgreSQL test database is unavailable", pytrace=False)
    os.environ["DATABASE_URL"] = test_url
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    os.environ["DATABASE_URL"] = original_database_url
    try:
        with psycopg.connect(admin_url, autocommit=True, connect_timeout=3) as connection:
            connection.execute("DROP DATABASE IF EXISTS resolveai_v2_test WITH (FORCE)")
    except psycopg.Error:
        pytest.fail("The isolated PostgreSQL test database could not be removed", pytrace=False)


@pytest.fixture()
def seeded_database(isolated_test_database: None) -> dict[str, str]:
    initialize_database()
    token_a = secrets.token_urlsafe(24)
    token_staff_a = secrets.token_urlsafe(24)
    token_b = secrets.token_urlsafe(24)
    token_engineer_a = secrets.token_urlsafe(24)
    token_engineer_b = secrets.token_urlsafe(24)
    with get_connection() as connection:
        connection.execute("TRUNCATE support.companies CASCADE")
        connection.execute("INSERT INTO support.companies (company_id, name) VALUES ('company-a', 'A'), ('company-b', 'B')")
        connection.execute("INSERT INTO support.users (user_id, company_id, name, role) VALUES ('admin-a', 'company-a', 'A', 'admin'), ('staff-a', 'company-a', 'Staff A', 'staff'), ('staff-b', 'company-b', 'B', 'staff'), ('engineer-a', 'company-a', 'Engineer A', 'engineer'), ('engineer-b', 'company-b', 'Engineer B', 'engineer')")
        connection.execute("INSERT INTO support.access_tokens (token_hash, user_id) VALUES (%s, 'admin-a'), (%s, 'staff-a'), (%s, 'staff-b'), (%s, 'engineer-a'), (%s, 'engineer-b')", (hash_token(token_a), hash_token(token_staff_a), hash_token(token_b), hash_token(token_engineer_a), hash_token(token_engineer_b)))
        connection.execute("INSERT INTO support.ticket_assignment_rules (company_id, engineer_user_id) VALUES ('company-a', 'engineer-a')")
        connection.execute("INSERT INTO merchant.shops (company_id, shop_id, channel, sync_enabled, connection_status) VALUES ('company-a', 'shop-a', 'A', TRUE, 'authorized'), ('company-a', 'shop-b', 'B', TRUE, 'authorized'), ('company-b', 'shop-b-company', 'B', TRUE, 'authorized')")
        connection.execute("INSERT INTO merchant.sku_mappings (company_id, shop_id, platform_sku, merchant_sku) VALUES ('company-a', 'shop-a', 'SKU-1', 'MERCHANT-SKU-1'), ('company-a', 'shop-b', 'SKU-1', 'MERCHANT-SKU-1'), ('company-b', 'shop-b-company', 'SKU-1', 'MERCHANT-SKU-1')")
        connection.execute("INSERT INTO merchant.stock_rules (company_id, shop_id, platform_sku, warehouse_sku, safety_stock) VALUES ('company-a', 'shop-a', 'SKU-1', 'MERCHANT-SKU-1', 5), ('company-a', 'shop-b', 'SKU-1', 'MERCHANT-SKU-1', 5), ('company-b', 'shop-b-company', 'SKU-1', 'MERCHANT-SKU-1', 5)")
    return {"token_a": token_a, "token_staff_a": token_staff_a, "token_b": token_b, "token_engineer_a": token_engineer_a, "token_engineer_b": token_engineer_b}


@pytest.fixture()
def fake_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    def embed(texts: list[str], task_type: str) -> list[list[float]]:
        vectors = []
        for index, _ in enumerate(texts):
            vector = [0.0] * 1024
            vector[index % 1024] = 1.0
            vectors.append(vector)
        return vectors

    monkeypatch.setattr("backend.app.customer_document_ingestion.embed_texts", embed)
