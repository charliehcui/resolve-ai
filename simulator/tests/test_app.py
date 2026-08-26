from fastapi.testclient import TestClient

from simulator.app import app

client = TestClient(app)


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
