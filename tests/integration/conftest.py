"""
Integration test configuration.

These tests require the test infrastructure to be running:
    make test-infra-up

Environment variables (set by docker-compose.test.yml or directly):
    TEST_DATABASE_URL   - PostgreSQL connection string (default: localhost:5433)
    TEST_REDIS_URL      - Redis connection string (default: localhost:6380)
    ORCHESTRATION_URL   - Orchestration service base URL (default: http://localhost:8000)
"""
import os
import pytest
import httpx


# Base URLs for running services (set these if testing against a live stack)
ORCHESTRATION_URL = os.getenv("ORCHESTRATION_URL", "http://localhost:8000")
IDENTITY_URL = os.getenv("IDENTITY_SERVICE_URL", "http://localhost:8001")
CATALOG_URL = os.getenv("CATALOG_SERVICE_URL", "http://localhost:8002")
SALES_URL = os.getenv("SALES_SERVICE_URL", "http://localhost:8003")
ROUTE_URL = os.getenv("ROUTE_SERVICE_URL", "http://localhost:8004")
ATTENDANCE_URL = os.getenv("ATTENDANCE_SERVICE_URL", "http://localhost:8005")
NOTIFICATION_URL = os.getenv("NOTIFICATION_SERVICE_URL", "http://localhost:8006")


@pytest.fixture(scope="session")
def http_client():
    """Shared httpx client for integration tests."""
    with httpx.Client(timeout=10.0) as client:
        yield client


@pytest.fixture(scope="session")
def service_urls():
    return {
        "orchestration": ORCHESTRATION_URL,
        "identity": IDENTITY_URL,
        "catalog": CATALOG_URL,
        "sales": SALES_URL,
        "route": ROUTE_URL,
        "attendance": ATTENDANCE_URL,
        "notification": NOTIFICATION_URL,
    }
