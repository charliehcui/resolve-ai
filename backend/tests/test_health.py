from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app import main

client = TestClient(main.app)


def test_liveness_endpoint() -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_liveness_endpoint_allows_frontend_origin() -> None:
    response = client.get("/health/live", headers={"Origin": "http://127.0.0.1:3000"})

    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"


def test_readiness_endpoint(monkeypatch) -> None:
    test_engine = create_engine("sqlite+pysqlite://")
    monkeypatch.setattr(main, "engine", test_engine)

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}

    test_engine.dispose()
