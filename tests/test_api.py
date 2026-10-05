from fastapi.testclient import TestClient

from backend.app.api import app


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_versioned_api_direct_human_ticket_and_engineer_scope(seeded_database: dict[str, str]) -> None:
    client = TestClient(app)
    started = client.post("/api/v1/conversations", headers=bearer(seeded_database["token_a"]))
    assert started.status_code == 200
    conversation_id = started.json()["conversation"]["conversation_id"]

    requested = client.post("/api/v1/tickets", headers=bearer(seeded_database["token_a"]), json={"conversation_id": conversation_id, "reason": "Please assign an engineer"})
    assert requested.status_code == 200
    ticket_id = requested.json()["ticket_id"]

    snapshot = client.get(f"/api/v1/conversations/{conversation_id}", headers=bearer(seeded_database["token_a"]))
    assert snapshot.status_code == 200
    assert snapshot.json()["ticket"]["ticket_id"] == ticket_id

    assigned = client.get("/api/v1/engineer/tickets", headers=bearer(seeded_database["token_engineer_a"]))
    assert assigned.status_code == 200
    assert [item["ticket_id"] for item in assigned.json()] == [ticket_id]
    rechecked = client.post(f"/api/v1/tickets/{ticket_id}/recheck", headers=bearer(seeded_database["token_engineer_a"]))
    assert rechecked.status_code == 200
    assert rechecked.json()["recheck_status"] == "NEEDS_INFO" and rechecked.json()["status"] == "open"
    exported = client.get(f"/api/v1/tickets/{ticket_id}/export", headers=bearer(seeded_database["token_engineer_a"]))
    assert exported.status_code == 200 and exported.headers["content-type"].startswith("text/html")
    assert ticket_id in exported.text and seeded_database["token_engineer_a"] not in exported.text
    denied = client.get(f"/api/v1/tickets/{ticket_id}", headers=bearer(seeded_database["token_engineer_b"]))
    assert denied.status_code == 403
    merchant_recheck = client.post(f"/api/v1/tickets/{ticket_id}/recheck", headers=bearer(seeded_database["token_a"]))
    assert merchant_recheck.status_code == 403


def test_api_openapi_and_authentication(seeded_database: dict[str, str]) -> None:
    client = TestClient(app)
    schema = client.get("/openapi.json").json()
    assert schema["info"]["version"] == "2.0.0"
    assert "/api/v1/conversations/{conversation_id}/messages" in schema["paths"]
    assert "/api/v1/actions/{action_id}/decision" in schema["paths"]
    assert "/api/v1/engineer/tickets" in schema["paths"]
    assert "/api/v1/tickets/{ticket_id}/recheck" in schema["paths"]
    assert "/api/v1/tickets/{ticket_id}/export" in schema["paths"]
    assert client.post("/api/v1/conversations").status_code == 403
