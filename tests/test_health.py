"""HTTP contract tests run in process without a listening network socket."""

import pytest
from fastapi.testclient import TestClient

from app import __version__
from app.config import Settings


def test_health_returns_service_status(client: TestClient) -> None:
    """The liveness contract contains exactly the public status and service name."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "enterprise-workflow-agent"}


def test_health_rejects_post(client: TestClient) -> None:
    """An unsupported method must not silently become a successful health request."""
    assert client.post("/health").status_code == 405


def test_unknown_route_returns_404(client: TestClient) -> None:
    """A business API is not advertised as implemented by an unknown URL."""
    assert client.get("/api/documents").status_code == 404


def test_openapi_describes_health_contract(client: TestClient) -> None:
    """Clients can discover the version and required response fields in OpenAPI."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["version"] == __version__
    assert schema["info"]["title"] == "enterprise-workflow-agent"
    assert "/health" in schema["paths"]
    # /health is the only endpoint that works without a token.
    assert "security" not in schema["paths"]["/health"]["get"]
    assert schema["components"]["schemas"]["HealthResponse"]["required"] == [
        "status",
        "service",
    ]


def test_app_instances_keep_their_settings_separate() -> None:
    """Creating a second app must not change the first app's service response."""
    from app.main import create_app

    first = create_app(Settings(app_name="first"))
    second = create_app(Settings(app_name="second"))
    with TestClient(first) as first_client, TestClient(second) as second_client:
        assert first_client.get("/health").json()["service"] == "first"
        assert second_client.get("/health").json()["service"] == "second"
        assert first_client.get("/health").json()["service"] == "first"


def test_debug_is_off_by_default() -> None:
    """A fresh application must not expose debug tracebacks by default."""
    from app.main import create_app

    assert create_app().debug is False


@pytest.mark.parametrize("path", ["/docs", "/redoc"])
def test_api_documentation_is_available(client: TestClient, path: str) -> None:
    """Both documented HTML entry points are served by the local API."""
    response = client.get(path)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
