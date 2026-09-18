from datetime import datetime, timedelta
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from backend.app.database import get_connection
from backend.app.handoff import handoff_to_support
from backend.app.models import AuthContext
from backend.app.stock import assess_stock_facts
from backend.app.support_tools import TOOL_FUNCTIONS, get_stock_facts, validate_tool_call
from simulator.services import common, merchant, worker
from simulator.services.merchant import app as merchant_app
from simulator.services.platform import app as platform_app
from simulator.services.warehouse import app as warehouse_app


def stock_runtime(seeded_database: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, TestClient, TestClient]:
    monkeypatch.setattr(common, "read_service_token", lambda name: "service-test-token")
    monkeypatch.setattr(merchant, "read_service_token", lambda name: "service-test-token")
    monkeypatch.setattr(worker, "read_service_token", lambda name: "service-test-token")
    warehouse_client = TestClient(warehouse_app)
    platform_client = TestClient(platform_app)
    merchant_client = TestClient(merchant_app)

    def warehouse_get(url: str, **kwargs):
        path = url.split("8003", 1)[-1]
        return warehouse_client.get(path, headers=kwargs.get("headers"))

    def platform_post(url: str, **kwargs):
        path = url.split("8001", 1)[-1]
        return platform_client.post(path, json=kwargs.get("json"), headers=kwargs.get("headers"))

    monkeypatch.setattr(merchant.httpx, "get", warehouse_get)
    monkeypatch.setattr(worker.httpx, "post", platform_post)
    return warehouse_client, merchant_client, platform_client


def set_warehouse_stock(client: TestClient, physical: int, reserved: int, company_id: str = "company-a") -> dict[str, object]:
    response = client.post("/lab/stocks/MERCHANT-SKU-1", params={"company_id": company_id}, json={"physical_quantity": physical, "reserved_quantity": reserved}, headers={"X-Lab-Token": "service-test-token"})
    assert response.status_code == 200
    return response.json()


def queue_publish(client: TestClient, token: str, shop_id: str = "shop-a") -> dict[str, object]:
    response = client.post("/stocks/publish", json={"shop_id": shop_id, "platform_sku": "SKU-1"}, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    return response.json()


def test_real_stock_publish_computes_65_and_platform_keeps_source_version(seeded_database: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    warehouse_client, merchant_client, platform_client = stock_runtime(seeded_database, monkeypatch)
    warehouse = set_warehouse_stock(warehouse_client, 80, 10)
    queued = queue_publish(merchant_client, seeded_database["token_a"])
    assert queued["expected_quantity"] == 65
    result = worker.process_next_stock_task()
    assert result and result["status"] == "completed"
    platform = platform_client.get("/internal/stocks/SKU-1", params={"shop_id": "shop-a"}, headers={"X-Service-Token": "service-test-token", "X-Company-ID": "company-a"})
    assert platform.status_code == 200
    assert platform.json()["quantity"] == 65
    assert platform.json()["source_version"] == warehouse["version"]

    def read_router(url: str, **kwargs):
        path = urlparse(url).path
        if "8002" in url:
            return merchant_client.get(path, params=kwargs.get("params"), headers=kwargs.get("headers"))
        if "8003" in url:
            return warehouse_client.get(path, params=kwargs.get("params"), headers=kwargs.get("headers"))
        return platform_client.get(path, params=kwargs.get("params"), headers=kwargs.get("headers"))

    monkeypatch.setattr("backend.app.support_tools.read_service_token", lambda name: "service-test-token")
    monkeypatch.setattr("backend.app.support_tools.httpx.get", read_router)
    facts = get_stock_facts(AuthContext(company_id="company-a", user_id="admin-a", role="admin"), "shop-a", "SKU-1")
    assert facts.status == "success"
    assert facts.response["assessment"] == "consistent" and facts.response["expected_quantity"] == 65


def test_failed_publish_leaves_old_120_and_time_window_controls_diagnosis(seeded_database: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    warehouse_client, merchant_client, platform_client = stock_runtime(seeded_database, monkeypatch)
    first = set_warehouse_stock(warehouse_client, 135, 10)
    queue_publish(merchant_client, seeded_database["token_a"])
    assert worker.process_next_stock_task()["status"] == "completed"
    old_platform = platform_client.get("/internal/stocks/SKU-1", params={"shop_id": "shop-a"}, headers={"X-Service-Token": "service-test-token", "X-Company-ID": "company-a"}).json()
    assert old_platform["quantity"] == 120 and old_platform["source_version"] == first["version"]
    latest = set_warehouse_stock(warehouse_client, 80, 10)
    with get_connection() as connection:
        connection.execute("UPDATE merchant.shops SET connection_status = 'internal_error' WHERE company_id = 'company-a' AND shop_id = 'shop-a'")
    queue_publish(merchant_client, seeded_database["token_a"])
    failed = worker.process_next_stock_task()
    assert failed["status"] == "failed" and failed["http_status"] == 500
    unchanged = platform_client.get("/internal/stocks/SKU-1", params={"shop_id": "shop-a"}, headers={"X-Service-Token": "service-test-token", "X-Company-ID": "company-a"}).json()
    assert unchanged["quantity"] == 120 and unchanged["source_version"] == first["version"]
    merchant_fact = {"empty": False, "rule": {"safety_stock": 5}}
    assert assess_stock_facts(merchant_fact, latest, unchanged, now=datetime.fromisoformat(str(latest["updated_at"]))) ["assessment"] == "waiting"
    later = datetime.fromisoformat(str(latest["updated_at"])) + timedelta(seconds=31)
    diagnosis = assess_stock_facts(merchant_fact, latest, unchanged, now=later)
    assert diagnosis["assessment"] == "difference" and diagnosis["reason"] == "VERSION_NOT_PUBLISHED"


def test_unknown_mapping_and_missing_version_do_not_force_numeric_comparison() -> None:
    unknown = assess_stock_facts({"empty": True, "reason": "STOCK_MAPPING_MISSING"}, None, None)
    incomplete = assess_stock_facts({"empty": False, "rule": {"safety_stock": 5}}, {"physical_quantity": 80, "reserved_quantity": 10}, None)
    assert unknown == {"assessment": "insufficient_information", "reason": "STOCK_MAPPING_MISSING"}
    assert incomplete["assessment"] == "insufficient_information"
    assert "expected_quantity" not in unknown and "expected_quantity" not in incomplete


def test_stock_contract_is_company_scoped(seeded_database: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    warehouse_client, _, platform_client = stock_runtime(seeded_database, monkeypatch)
    set_warehouse_stock(warehouse_client, 80, 10)
    headers_b = {"X-Service-Token": "service-test-token", "X-Company-ID": "company-b"}
    assert warehouse_client.get("/internal/stocks/MERCHANT-SKU-1", headers=headers_b).status_code == 404
    assert platform_client.get("/internal/stocks/SKU-1", params={"shop_id": "shop-a"}, headers=headers_b).status_code == 404


def test_stock_handoff_and_tool_scope_need_shop_and_sku_not_order(seeded_database: dict[str, str]) -> None:
    from backend.app.auth import authenticate
    from backend.app.database import create_conversation

    auth: AuthContext = authenticate(seeded_database["token_a"])
    conversation_id = create_conversation(auth.company_id, auth.user_id)
    handoff, _ = handoff_to_support(auth, conversation_id, "shop-a 的 SKU-1 库存为什么不同", "需要后台调查", [])
    assert handoff.known_shop_id == "shop-a" and handoff.known_sku == "SKU-1"
    assert handoff.known_order_id is None and handoff.missing_fields == []
    assert validate_tool_call({"name": "GetStockFacts", "args": {"shop_id": "shop-a", "sku": "SKU-1"}}, "shop-a", "", "SKU-1") is None
    assert set(TOOL_FUNCTIONS).isdisjoint({"SetStock", "PublishStock", "AdjustStock"})


def test_stock_handoff_without_sku_requests_sku_not_order(seeded_database: dict[str, str]) -> None:
    from backend.app.auth import authenticate
    from backend.app.database import create_conversation

    auth = authenticate(seeded_database["token_a"])
    conversation_id = create_conversation(auth.company_id, auth.user_id)
    handoff, _ = handoff_to_support(auth, conversation_id, "shop-a 的库存为什么不同", "需要后台调查", [])
    assert handoff.missing_fields == ["sku"]
