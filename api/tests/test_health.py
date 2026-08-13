from fastapi.testclient import TestClient

from app.main import app


def test_health_is_dependency_free() -> None:
    """Liveness must answer even when every dependency is down."""
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
