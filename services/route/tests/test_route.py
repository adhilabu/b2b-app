"""
Route service tests — VRP solver unit tests and beat plan HTTP endpoints.

All VRP tests are pure unit tests (no database, no HTTP).
Beat plan endpoint tests use FastAPI TestClient with SQLite in-memory.
"""
import pytest
import pytest_asyncio
import uuid
from datetime import date, datetime
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.optimizer.vrp_solver import (
    Stop, optimize_route, haversine_km, build_distance_matrix,
    _greedy_nearest_neighbor,
)


# ─────────────────────────────────────────────
# TEST DATABASE SETUP
# ─────────────────────────────────────────────

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


def _mock_settings():
    """Return a settings object whose JWT public key is a dummy value."""
    from unittest.mock import MagicMock
    s = MagicMock()
    s.jwt_public_key = "dummy"
    s.JWT_ALGORITHM = "RS256"
    s.DATABASE_URL = TEST_DB_URL
    s.DEBUG = False
    s.CORS_ORIGINS = []
    return s


# ─────────────────────────────────────────────
# CLASS: TestHaversine
# ─────────────────────────────────────────────

class TestHaversine:
    """Unit tests for the haversine great-circle distance formula."""

    def test_same_point_is_zero(self):
        """Distance from a point to itself must be exactly 0."""
        assert haversine_km(12.9716, 77.5946, 12.9716, 77.5946) == pytest.approx(0.0)

    def test_bangalore_to_mysore(self):
        """Bangalore to Mysore is approximately 128 km great-circle."""
        dist = haversine_km(12.9716, 77.5946, 12.2958, 76.6394)
        assert 120 < dist < 140

    def test_london_to_paris(self):
        """London (51.5074, -0.1278) to Paris (48.8566, 2.3522) is ~340 km."""
        dist = haversine_km(51.5074, -0.1278, 48.8566, 2.3522)
        assert 330 < dist < 360

    def test_symmetry(self):
        """d(A, B) must equal d(B, A) — the formula is symmetric."""
        d1 = haversine_km(12.9716, 77.5946, 13.0, 77.6)
        d2 = haversine_km(13.0, 77.6, 12.9716, 77.5946)
        assert d1 == pytest.approx(d2)

    def test_north_south_only_movement(self):
        """Moving 1 degree north along the same meridian is ~111 km."""
        dist = haversine_km(0.0, 0.0, 1.0, 0.0)
        assert 110 < dist < 112

    def test_antipodal_points_are_half_circumference(self):
        """Antipodal points are ~20,015 km apart (half Earth circumference)."""
        dist = haversine_km(0.0, 0.0, 0.0, 180.0)
        assert 20000 < dist < 20050

    def test_very_close_points_small_distance(self):
        """Two points 0.001 degree apart should be well under 1 km."""
        dist = haversine_km(12.9716, 77.5946, 12.9726, 77.5956)
        assert dist < 1.0
        assert dist > 0.0


# ─────────────────────────────────────────────
# CLASS: TestDistanceMatrix
# ─────────────────────────────────────────────

class TestDistanceMatrix:
    """Unit tests for build_distance_matrix."""

    def test_single_stop_matrix_is_zero(self):
        """A single-stop matrix has only [0] on the diagonal."""
        stops = [Stop("a", 12.97, 77.59)]
        matrix = build_distance_matrix(stops)
        assert matrix == [[0]]

    def test_diagonal_is_always_zero(self):
        """Self-to-self distance must be 0 for all stops."""
        stops = [
            Stop("a", 12.97, 77.59),
            Stop("b", 13.00, 77.62),
            Stop("c", 12.95, 77.55),
        ]
        matrix = build_distance_matrix(stops)
        for i in range(len(stops)):
            assert matrix[i][i] == 0

    def test_matrix_is_symmetric(self):
        """Distance matrix must be symmetric: matrix[i][j] == matrix[j][i]."""
        stops = [
            Stop("a", 12.97, 77.59),
            Stop("b", 13.00, 77.62),
            Stop("c", 12.95, 77.55),
        ]
        matrix = build_distance_matrix(stops)
        n = len(stops)
        for i in range(n):
            for j in range(n):
                assert matrix[i][j] == matrix[j][i]

    def test_matrix_values_are_integers(self):
        """build_distance_matrix must return integer values (OR-Tools requirement)."""
        stops = [
            Stop("a", 12.97, 77.59),
            Stop("b", 13.00, 77.62),
        ]
        matrix = build_distance_matrix(stops)
        assert isinstance(matrix[0][1], int)
        assert isinstance(matrix[1][0], int)

    def test_matrix_nonzero_for_distinct_points(self):
        """Off-diagonal entries for geographically separate stops must be > 0."""
        stops = [
            Stop("a", 12.97, 77.59),
            Stop("b", 13.00, 77.62),
        ]
        matrix = build_distance_matrix(stops)
        assert matrix[0][1] > 0
        assert matrix[1][0] > 0

    def test_matrix_dimensions_match_stop_count(self):
        """Matrix dimensions must equal the number of stops."""
        n = 4
        stops = [Stop(f"s{i}", 12.0 + i * 0.1, 77.0 + i * 0.1) for i in range(n)]
        matrix = build_distance_matrix(stops)
        assert len(matrix) == n
        assert all(len(row) == n for row in matrix)

    def test_same_coordinates_yields_zero_distance(self):
        """Two stops at identical coordinates have zero distance between them."""
        stops = [
            Stop("a", 12.97, 77.59),
            Stop("b", 12.97, 77.59),  # Same coords
        ]
        matrix = build_distance_matrix(stops)
        assert matrix[0][1] == 0
        assert matrix[1][0] == 0


# ─────────────────────────────────────────────
# CLASS: TestVRPSolver
# ─────────────────────────────────────────────

class TestVRPSolver:
    """Unit tests for the optimize_route function and greedy fallback."""

    def test_empty_stops_returns_empty_list(self):
        """optimize_route with zero stops returns an empty list."""
        result = optimize_route([])
        assert result == []

    def test_single_stop_returns_index_zero(self):
        """Single stop must be returned as [0] (trivial case)."""
        stops = [Stop("s1", 12.97, 77.59)]
        result = optimize_route(stops)
        assert result == [0]

    def test_two_stops_returns_both_indices(self):
        """Two stops must produce a route containing both indices."""
        stops = [
            Stop("depot", 12.97, 77.59),
            Stop("s1", 12.98, 77.60),
        ]
        result = optimize_route(stops)
        assert len(result) == 2
        assert set(result) == {0, 1}

    def test_triangle_of_stops_all_present(self):
        """Three stops in a triangle must all appear in the optimized sequence."""
        stops = [
            Stop("a", 12.97, 77.59),
            Stop("b", 13.00, 77.62),
            Stop("c", 12.94, 77.56),
        ]
        result = optimize_route(stops)
        assert len(result) == 3
        assert set(result) == {0, 1, 2}

    def test_no_duplicate_stops_in_route(self):
        """The route must not revisit any stop (no duplicates)."""
        stops = [
            Stop("a", 12.97, 77.59),
            Stop("b", 12.99, 77.61),
            Stop("c", 13.01, 77.63),
        ]
        result = optimize_route(stops)
        assert len(result) == len(set(result))

    def test_sequence_numbers_are_zero_to_n_minus_one(self):
        """Returned indices must be exactly 0..n-1 (one visit per stop)."""
        n = 5
        stops = [Stop(f"s{i}", 12.97 + i * 0.01, 77.59 + i * 0.01) for i in range(n)]
        result = optimize_route(stops)
        assert sorted(result) == list(range(n))

    def test_optimize_route_visits_all_stops_five(self):
        """Regardless of solver, all 5 stops must be included in output."""
        stops = [
            Stop(f"stop-{i}", 12.97 + i * 0.01, 77.59 + i * 0.01)
            for i in range(5)
        ]
        result = optimize_route(stops)
        assert len(result) == len(stops)
        assert set(result) == set(range(len(stops)))

    def test_stops_with_same_coordinates(self):
        """Stops at identical coordinates are still all visited once."""
        stops = [
            Stop("a", 12.97, 77.59),
            Stop("b", 12.97, 77.59),
            Stop("c", 12.97, 77.59),
        ]
        result = optimize_route(stops)
        assert len(result) == 3
        assert set(result) == {0, 1, 2}

    def test_depot_index_is_first_in_greedy(self):
        """Greedy nearest-neighbor starts from the given depot index."""
        stops = [
            Stop("a", 12.97, 77.59),
            Stop("b", 13.00, 77.62),
            Stop("c", 12.95, 77.55),
        ]
        result = _greedy_nearest_neighbor(stops, 0)
        assert result[0] == 0  # Always starts from depot

    def test_greedy_fallback_visits_all_four_stops(self):
        """Greedy fallback covers every stop exactly once."""
        stops = [
            Stop("a", 12.97, 77.59),
            Stop("b", 13.00, 77.62),
            Stop("c", 12.95, 77.55),
            Stop("d", 13.05, 77.65),
        ]
        result = _greedy_nearest_neighbor(stops, 0)
        assert len(result) == len(stops)
        assert set(result) == set(range(len(stops)))

    def test_greedy_fallback_no_duplicates(self):
        """Greedy fallback must not revisit any stop."""
        stops = [Stop(f"s{i}", 12.0 + i * 0.05, 77.0 + i * 0.05) for i in range(6)]
        result = _greedy_nearest_neighbor(stops, 0)
        assert len(result) == len(set(result))

    def test_ortools_fallback_on_import_error(self):
        """When OR-Tools is not importable the greedy fallback is used and still produces a valid route."""
        stops = [
            Stop("a", 12.97, 77.59),
            Stop("b", 13.00, 77.62),
            Stop("c", 12.95, 77.55),
        ]
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name.startswith("ortools"):
                raise ImportError("OR-Tools not available")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            result = optimize_route(stops)

        assert len(result) == len(stops)
        assert set(result) == {0, 1, 2}


# ─────────────────────────────────────────────
# BEAT PLAN ENDPOINT TESTS
# ─────────────────────────────────────────────

def _make_token_payload(tenant_id: str | None = None, user_id: str | None = None) -> dict:
    import time
    return {
        "sub": user_id or str(uuid.uuid4()),
        "tenant_id": tenant_id or str(uuid.uuid4()),
        "roles": ["sales_rep"],
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        "jti": str(uuid.uuid4()),
    }


@pytest_asyncio.fixture(scope="module")
async def beat_client():
    """Shared async HTTP client for beat plan endpoint tests."""
    from app.main import app, Base, get_db, get_current_user, TokenPayload

    engine = create_async_engine(TEST_DB_URL, echo=False)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    payload = TokenPayload(**_make_token_payload())

    async def override_db():
        async with Session() as s:
            yield s

    async def override_auth():
        return payload

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_auth

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_health(beat_client):
    """Health endpoint responds 200 with the correct service name."""
    r = await beat_client.get("/health")
    assert r.status_code == 200
    assert r.json()["service"] == "route"


@pytest.mark.asyncio
async def test_create_beat_with_no_stops(beat_client):
    """A beat with an empty stops list can be created successfully."""
    r = await beat_client.post("/beats/", json={
        "name": "Empty Beat",
        "scheduled_date": str(date.today()),
        "stops": [],
    })
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "Empty Beat"
    assert data["is_optimized"] is False


@pytest.mark.asyncio
async def test_create_beat_with_stops(beat_client):
    """Beat creation with stops sets their sequence numbers starting at 0."""
    stops = [
        {"customer_id": str(uuid.uuid4()), "customer_name": f"Shop {i}",
         "latitude": 12.97 + i * 0.01, "longitude": 77.59 + i * 0.01}
        for i in range(3)
    ]
    r = await beat_client.post("/beats/", json={
        "name": "Morning Beat",
        "scheduled_date": str(date.today()),
        "stops": stops,
    })
    assert r.status_code == 201
    assert r.json()["name"] == "Morning Beat"


@pytest.mark.asyncio
async def test_list_beats_returns_list(beat_client):
    """List endpoint returns a JSON array."""
    r = await beat_client.get("/beats/")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_get_beat_detail_returns_stops(beat_client):
    """GET /beats/{id} returns stops with a sequence attribute."""
    create_r = await beat_client.post("/beats/", json={
        "name": "Detail Test Beat",
        "scheduled_date": str(date.today()),
        "stops": [
            {"customer_id": str(uuid.uuid4()), "customer_name": "Stop A",
             "latitude": 12.97, "longitude": 77.59},
        ],
    })
    beat_id = create_r.json()["id"]

    r = await beat_client.get(f"/beats/{beat_id}")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == beat_id
    assert isinstance(data["stops"], list)
    assert data["stops"][0]["sequence"] == 0


@pytest.mark.asyncio
async def test_get_nonexistent_beat_returns_404(beat_client):
    """Requesting a beat with an unknown ID returns 404."""
    r = await beat_client.get(f"/beats/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_optimize_beat_sequence_integrity(beat_client):
    """After optimization, the sequence numbers must be contiguous 0..n-1."""
    stops = [
        {"customer_id": str(uuid.uuid4()), "customer_name": f"Stop {i}",
         "latitude": 12.97 + i * 0.01, "longitude": 77.59 + i * 0.01}
        for i in range(4)
    ]
    create_r = await beat_client.post("/beats/", json={
        "name": "Optimize Me",
        "scheduled_date": str(date.today()),
        "stops": stops,
    })
    beat_id = create_r.json()["id"]

    opt_r = await beat_client.post(f"/beats/{beat_id}/optimize")
    assert opt_r.status_code == 200
    data = opt_r.json()
    assert data["is_optimized"] is True
    assert len(data["optimized_sequence"]) == 4

    # Fetch the beat detail and verify sequence integrity
    detail_r = await beat_client.get(f"/beats/{beat_id}")
    sequences = [s["sequence"] for s in detail_r.json()["stops"]]
    assert sorted(sequences) == list(range(4))


@pytest.mark.asyncio
async def test_list_beats_filtered_by_date(beat_client):
    """List endpoint filters correctly by scheduled_date."""
    future_date = "2099-12-31"
    await beat_client.post("/beats/", json={
        "name": "Far Future Beat",
        "scheduled_date": future_date,
        "stops": [],
    })
    r = await beat_client.get(f"/beats/?scheduled_date={future_date}")
    assert r.status_code == 200
    data = r.json()
    assert all(b["scheduled_date"] == future_date for b in data)
