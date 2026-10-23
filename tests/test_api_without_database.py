"""Business endpoints exist but answer 503 until DATABASE_URL is configured."""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/me"),
        ("get", "/workflows/WF-1"),
        ("post", "/workflows/WF-1/check"),
        ("get", "/reconciliations/00000000-0000-0000-0000-000000000000"),
    ],
)
def test_business_endpoints_need_a_database(
    client: TestClient, method: str, path: str
) -> None:
    """The reason is explicit and the liveness endpoint keeps working."""
    response = client.request(method, path, headers={"Authorization": "Bearer x"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_not_configured"
    assert client.get("/health").status_code == 200


def test_openapi_declares_bearer_security(client: TestClient) -> None:
    """Protected operations advertise the bearer scheme; commands need a key."""
    schema = client.get("/openapi.json").json()
    assert schema["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    approve = schema["paths"]["/workflows/{workflow_id}/approve"]["post"]
    assert approve["security"] == [{"HTTPBearer": []}]
    headers = {p["name"] for p in approve["parameters"] if p["in"] == "header"}
    assert headers == {"Idempotency-Key"}
    assert (
        "application/octet-stream"
        in (schema["paths"]["/documents"]["post"]["requestBody"]["content"])
    )
