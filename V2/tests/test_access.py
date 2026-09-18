import pytest
from fastapi.testclient import TestClient

from backend.app.auth import authenticate, authorize_conversation
from backend.app.config import Settings, require_env
from backend.app.database import create_conversation, load_messages, save_message
from backend.app.models import provider_for_task
from simulator.services.common import OrderEvent
from simulator.services.merchant import app as merchant_app
from simulator.services.merchant import store_order_event


def test_invalid_token_is_rejected(seeded_database: dict[str, str]) -> None:
    with pytest.raises(PermissionError, match="Invalid access token"):
        authenticate("not-a-valid-token")


def test_company_b_cannot_open_company_a_conversation(seeded_database: dict[str, str]) -> None:
    auth_a = authenticate(seeded_database["token_a"])
    auth_b = authenticate(seeded_database["token_b"])
    conversation_id = create_conversation(auth_a.company_id, auth_a.user_id)
    with pytest.raises(PermissionError, match="not available"):
        authorize_conversation(auth_b, conversation_id)


def test_same_user_can_continue_conversation(seeded_database: dict[str, str]) -> None:
    auth = authenticate(seeded_database["token_a"])
    conversation_id = create_conversation(auth.company_id, auth.user_id)
    save_message(conversation_id, "user", "第一个问题")
    authorize_conversation(auth, conversation_id)
    assert [message["content"] for message in load_messages(conversation_id)] == ["第一个问题"]


def test_company_cannot_read_other_company_order_task(seeded_database: dict[str, str]) -> None:
    order_event = OrderEvent(
        event_id="19a75c1b-4f8e-45ac-9145-ab013baf973f",
        company_id="company-a",
        shop_id="shop-a",
        external_order_id="O-SCOPED",
        sku="SKU-1",
        quantity=1,
        amount_minor=1000,
        payment_status="paid",
    )
    store_order_event(order_event)
    response = TestClient(merchant_app).get(
        f"/tasks/{order_event.event_id}",
        headers={"Authorization": f"Bearer {seeded_database['token_b']}"},
    )
    assert response.status_code == 404


def test_order_event_ingest_requires_service_credential(seeded_database: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("simulator.services.common.read_service_token", lambda name: "expected-service-token")
    order_event = OrderEvent(
        event_id="af32fb28-1871-4ec1-bcf0-5b0dd6b4f9cc",
        company_id="company-a",
        shop_id="shop-a",
        external_order_id="O-NO-CREDENTIAL",
        sku="SKU-1",
        quantity=1,
        amount_minor=1000,
        payment_status="paid",
    )
    response = TestClient(merchant_app).post("/events/orders", json=order_event.model_dump())
    assert response.status_code == 401


def test_model_routing_is_deterministic() -> None:
    assert provider_for_task("customer_answer") == "groq"
    assert provider_for_task("query_rewrite") == "groq"
    assert provider_for_task("support_investigation") == "google"
    assert provider_for_task("parallel_tool_calling") == "google"


def test_missing_required_environment_variable_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PHASE_1_REQUIRED_VALUE", raising=False)
    with pytest.raises(RuntimeError, match="Missing required environment variable: PHASE_1_REQUIRED_VALUE"):
        require_env("PHASE_1_REQUIRED_VALUE")


def test_settings_repr_hides_secrets() -> None:
    settings = Settings(
        database_url="postgresql://user:database-secret@localhost/example",
        groq_api_key="groq-secret-value",
        groq_model="groq-model",
        google_api_key="google-secret-value",
        google_model="google-model",
        google_fallback_model="google-fallback-model",
        google_embedding_model="embedding-model",
        embedding_dimension=1024,
        rerank_model="rerank-model",
        retrieval_mode="hybrid_rerank",
        langsmith_tracing=True,
        langsmith_project="test-project",
    )
    settings_text = repr(settings)
    assert "database-secret" not in settings_text
    assert "groq-secret-value" not in settings_text
    assert "google-secret-value" not in settings_text
