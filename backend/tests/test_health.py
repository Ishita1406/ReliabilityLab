from collections.abc import Iterator
from importlib.metadata import version

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        service_name="test-service", environment="test"
    )
    with TestClient(app) as test_client:
        yield test_client


def test_health_returns_ok_status(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {
        "status": "ok",
        "service": "test-service",
        "version": version("ai-reliability-lab-backend"),
        "environment": "test",
    }


def test_health_rejects_non_get_methods(client: TestClient) -> None:
    response = client.post("/health")

    assert response.status_code == 405
