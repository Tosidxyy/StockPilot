from fastapi.testclient import TestClient

from app.main import app
from app import __version__


def test_health_returns_ok() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_reports_application_release_version() -> None:
    response = TestClient(app).get("/openapi.json")
    assert response.status_code == 200
    assert app.version == __version__ == "0.2.0"
    assert response.json()["info"]["version"] == __version__
    assert "/api/stocks/{code}/sentiment/analysis" in response.json()["paths"]
