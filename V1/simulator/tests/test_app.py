import pytest
from fastapi.testclient import TestClient

import simulator.app as simulator

client = TestClient(simulator.app)


@pytest.fixture(autouse=True)
def reset_action_state() -> None:
    simulator.reset_action_state()


def test_get_customer_account() -> None:
    response = client.get("/customers/customer_001")

    assert response.status_code == 200
    assert response.json()["status"] == "active"
    assert response.json()["event_notifications_enabled"] is True


def test_get_customer_product_context_returns_only_customer_visible_data() -> None:
    response = client.get("/customers/customer_001/product-context")

    assert response.status_code == 200
    assert response.json() == {
        "account_status": "active",
        "product_version": "2026.8",
        "affected_feature": "order notifications",
        "feature_enabled": True,
    }


def test_get_recent_customer_activity_returns_only_the_latest_simple_result() -> None:
    response = client.get("/customers/customer_001/recent-activity")
    response_data = response.json()

    assert response.status_code == 200
    assert set(response_data) == {"affected_feature", "activity", "result", "occurred_at"}
    assert response_data["result"] == "failed"
    assert response_data["occurred_at"] == "2026-08-25T09:20:00Z"


def test_get_event_notification_deliveries_for_known_customer() -> None:
    response = client.get("/customers/customer_001/event-notification-deliveries")

    assert response.status_code == 200

    deliveries = response.json()

    assert len(deliveries) == 2
    assert deliveries[0]["delivery_id"] == "delivery_001"
    assert deliveries[0]["customer_id"] == "customer_001"
    assert deliveries[0]["response_status"] == 401
    assert deliveries[1]["delivery_id"] == "delivery_002"
    assert deliveries[1]["response_status"] == 401


def test_get_event_notification_deliveries_for_unknown_customer() -> None:
    response = client.get("/customers/unknown_customer/event-notification-deliveries")

    assert response.status_code == 404
    assert response.json() == {"detail": "Customer event notification deliveries not found"}


def test_get_platform_status() -> None:
    response = client.get("/platform-status")

    assert response.status_code == 200
    assert response.json()["service"] == "event_notifications"
    assert response.json()["status"] == "operational"


@pytest.mark.parametrize("customer_id, feature, enabled, result", [("customer_001", "order notifications", True, "failed"), ("customer_002", "order notifications", False, "disabled"), ("customer_003", "report exports", True, "failed"), ("customer_004", "report exports", True, "failed")])
def test_scenarios_expose_only_customer_visible_context(customer_id, feature, enabled, result):
    context = client.get(f"/customers/{customer_id}/product-context").json()
    activity = client.get(f"/customers/{customer_id}/recent-activity").json()

    assert context["affected_feature"] == feature
    assert context["feature_enabled"] is enabled
    assert activity["result"] == result
    assert set(context) == {"account_status", "product_version", "affected_feature", "feature_enabled"}
    assert set(activity) == {"affected_feature", "activity", "result", "occurred_at"}
    assert "true_cause" not in str(context)
    assert "failure_code" not in str(activity)


def test_export_operations_are_customer_scoped_and_read_only():
    retry_operation = client.get("/customers/customer_003/background-operation").json()
    unknown_operation = client.get("/customers/customer_004/background-operation").json()

    assert retry_operation["operation_id"] == "export_003"
    assert retry_operation["customer_id"] == "customer_003"
    assert retry_operation["failure_code"] == "dependency_timeout"
    assert retry_operation["retry_allowed"] is True
    assert unknown_operation["operation_id"] == "export_004"
    assert unknown_operation["retry_allowed"] is False
    assert unknown_operation["status"] != unknown_operation["latest_run_status"]
    assert client.get("/customers/customer_001/background-operation").status_code == 404
    assert client.get("/customers/unknown/background-operation").status_code == 404
    assert client.post("/customers/customer_003/background-operation").status_code == 405
    assert client.get("/customers/customer_003/background-operation").json() == retry_operation


def test_platform_status_service_is_limited_to_known_features():
    assert client.get("/platform-status?service=report_exports").json()["service"] == "report_exports"
    assert client.get("/platform-status?service=unknown").status_code == 422


def test_retry_background_operation_uses_idempotency_key_once() -> None:
    request = {"idempotency_key": "ticket-3:retry_failed_operation:export_003"}
    first_response = client.post("/customers/customer_003/background-operations/export_003/retry", json=request)
    second_response = client.post("/customers/customer_003/background-operations/export_003/retry", json=request)
    different_key_response = client.post("/customers/customer_003/background-operations/export_003/retry", json={"idempotency_key": "different-key"})
    wrong_target_response = client.post("/customers/customer_004/background-operations/export_004/retry", json=request)

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert second_response.json() == first_response.json()
    assert first_response.json()["operation"]["status"] == "succeeded"
    assert simulator.retry_execution_counts[request["idempotency_key"]] == 1
    assert different_key_response.status_code == 409
    assert wrong_target_response.status_code == 409
