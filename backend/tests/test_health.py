from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_application_initialization() -> None:
    assert app.title == "HID Face Liveness Detection System"
    assert app.version == "0.1.0"


def test_health_endpoint_success() -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()

    assert payload["status"] == "healthy"
    assert payload["application"] == "HID Face Liveness Detection System"
    assert payload["version"] == "0.1.0"
    assert list(payload.keys()) == ["status", "application", "version"]
