"""Route service tests — VRP solver and beat plan endpoints."""
import pytest
import uuid
from app.optimizer.vrp_solver import Stop, optimize_route, haversine_km, _greedy_nearest_neighbor


class TestHaversine:
    def test_same_point_is_zero(self):
        assert haversine_km(12.9716, 77.5946, 12.9716, 77.5946) == pytest.approx(0.0)

    def test_bangalore_to_mysore(self):
        # ~128 km great-circle distance
        dist = haversine_km(12.9716, 77.5946, 12.2958, 76.6394)
        assert 120 < dist < 140

    def test_symmetry(self):
        d1 = haversine_km(12.9716, 77.5946, 13.0, 77.6)
        d2 = haversine_km(13.0, 77.6, 12.9716, 77.5946)
        assert d1 == pytest.approx(d2)


class TestVRPSolver:
    def test_single_stop_returns_as_is(self):
        stops = [Stop("s1", 12.97, 77.59)]
        result = optimize_route(stops)
        assert result == [0]

    def test_two_stops_returns_valid_route(self):
        stops = [
            Stop("depot", 12.97, 77.59),
            Stop("s1", 12.98, 77.60),
        ]
        result = optimize_route(stops)
        assert len(result) == 2
        assert set(result) == {0, 1}

    def test_greedy_fallback_visits_all(self):
        stops = [
            Stop("a", 12.97, 77.59),
            Stop("b", 13.00, 77.62),
            Stop("c", 12.95, 77.55),
            Stop("d", 13.05, 77.65),
        ]
        result = _greedy_nearest_neighbor(stops, 0)
        assert len(result) == len(stops)
        assert set(result) == set(range(len(stops)))

    def test_optimize_route_visits_all_stops(self):
        """Regardless of solver, all stops must be included in output."""
        stops = [
            Stop(f"stop-{i}", 12.97 + i * 0.01, 77.59 + i * 0.01)
            for i in range(5)
        ]
        result = optimize_route(stops)
        assert len(result) == len(stops)
        assert set(result) == set(range(len(stops)))

    def test_no_duplicate_stops_in_route(self):
        stops = [
            Stop("a", 12.97, 77.59),
            Stop("b", 12.99, 77.61),
            Stop("c", 13.01, 77.63),
        ]
        result = optimize_route(stops)
        assert len(result) == len(set(result))  # No duplicates


@pytest.mark.asyncio
async def test_health():
    """Smoke test the health endpoint."""
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/health")
    assert r.status_code == 200
    assert r.json()["service"] == "route"
