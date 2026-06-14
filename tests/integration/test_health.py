"""
Integration smoke tests: verify all services are healthy and reachable.

Run with: make test-integration  (requires full stack to be up)
Or: pytest tests/integration/ -v
"""
import pytest


SERVICE_HEALTH_URLS = [
    ("orchestration", "http://localhost:8000/health"),
    ("identity",      "http://localhost:8001/health"),
    ("catalog",       "http://localhost:8002/health"),
    ("sales",         "http://localhost:8003/health"),
    ("route",         "http://localhost:8004/health"),
    ("attendance",    "http://localhost:8005/health"),
    ("notification",  "http://localhost:8006/health"),
]


@pytest.mark.parametrize("service,url", SERVICE_HEALTH_URLS)
def test_service_health(http_client, service, url):
    """All services must respond 200 to their /health endpoint."""
    resp = http_client.get(url)
    assert resp.status_code == 200, f"{service} health check failed: {resp.text}"
    data = resp.json()
    assert data.get("status") == "healthy", f"{service} returned unhealthy: {data}"


def test_orchestration_lists_services(http_client):
    """Orchestration root endpoint lists all downstream services."""
    resp = http_client.get("http://localhost:8000/")
    assert resp.status_code == 200
    data = resp.json()
    assert "services" in data
    expected = {"identity", "catalog", "sales", "route", "attendance", "notification"}
    found = set(data["services"])
    assert expected.issubset(found)


def test_unauthenticated_protected_route_returns_401(http_client):
    """Protected endpoints must reject requests without a Bearer token."""
    resp = http_client.get("http://localhost:8000/sales/orders/")
    assert resp.status_code == 401


def test_login_with_invalid_credentials_returns_401(http_client):
    """Login with wrong credentials must return 401."""
    resp = http_client.post(
        "http://localhost:8000/auth/login",
        json={"email": "nobody@example.com", "password": "wrongpassword"},
    )
    assert resp.status_code in (401, 422)  # 422 if schema mismatch


class TestSyncProtocolSmoke:
    """Smoke tests for the offline sync push/pull endpoints."""

    def test_sync_push_requires_auth(self, http_client):
        resp = http_client.post(
            "http://localhost:8000/sync/push",
            json={"device_id": "test-device", "events": []},
        )
        assert resp.status_code == 401

    def test_sync_pull_requires_auth(self, http_client):
        resp = http_client.get("http://localhost:8000/sync/pull")
        assert resp.status_code == 401
