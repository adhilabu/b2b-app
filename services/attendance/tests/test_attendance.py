"""
Attendance service tests — check-in/out, leave management, availability blocking.

Uses FastAPI TestClient with SQLite in-memory. No real database or JWT keys required.
The get_current_user dependency is overridden with a mock TokenPayload for all tests.
"""
import pytest
import pytest_asyncio
import uuid
from datetime import date, timedelta, datetime
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from unittest.mock import patch

from app.main import (
    app, get_db, get_current_user, TokenPayload, Base,
    AttendanceStatus, LeaveStatus, LeaveType,
    Attendance, LeaveRequest,
)

# ─────────────────────────────────────────────
# TEST DATABASE SETUP
# ─────────────────────────────────────────────

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSession = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


def _mock_user(role: str = "sales_rep") -> TokenPayload:
    """Build a fake TokenPayload — no JWT verification needed."""
    return TokenPayload(
        sub=str(uuid.uuid4()),
        tenant_id=str(uuid.uuid4()),
        roles=[role],
        exp=9999999999,
        iat=1000000000,
        jti=str(uuid.uuid4()),
    )


# Stable mock users reused across tests
MOCK_SALES = _mock_user("sales_rep")
MOCK_MANAGER = _mock_user("manager")


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_test_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db():
    """Provide a fresh AsyncSession for each test."""
    async with TestSession() as session:
        yield session


@pytest_asyncio.fixture
async def client_sales(db):
    """HTTP client authenticated as the MOCK_SALES user."""
    async def override_db():
        yield db

    async def override_auth():
        return MOCK_SALES

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_auth
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client_manager(db):
    """HTTP client authenticated as the MOCK_MANAGER user."""
    async def override_db():
        yield db

    async def override_auth():
        return MOCK_MANAGER

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_auth
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


# ─────────────────────────────────────────────
# CLASS: TestEnums
# ─────────────────────────────────────────────

class TestEnums:
    """Verify enum members and their string values."""

    def test_attendance_status_values(self):
        """AttendanceStatus covers all required field states."""
        assert AttendanceStatus.present.value == "present"
        assert AttendanceStatus.absent.value == "absent"
        assert AttendanceStatus.on_leave.value == "on_leave"
        assert AttendanceStatus.half_day.value == "half_day"
        assert AttendanceStatus.work_from_home.value == "work_from_home"

    def test_leave_status_lifecycle_values(self):
        """LeaveStatus covers the full approval lifecycle."""
        assert LeaveStatus.pending.value == "pending"
        assert LeaveStatus.approved.value == "approved"
        assert LeaveStatus.rejected.value == "rejected"
        assert LeaveStatus.cancelled.value == "cancelled"

    def test_leave_type_values(self):
        """LeaveType covers all standard leave categories."""
        assert LeaveType.casual.value == "casual"
        assert LeaveType.sick.value == "sick"
        assert LeaveType.earned.value == "earned"
        assert LeaveType.unpaid.value == "unpaid"

    def test_all_attendance_statuses_are_distinct(self):
        """All AttendanceStatus values are unique strings."""
        values = [s.value for s in AttendanceStatus]
        assert len(values) == len(set(values))

    def test_all_leave_types_are_distinct(self):
        """All LeaveType values are unique strings."""
        values = [lt.value for lt in LeaveType]
        assert len(values) == len(set(values))


# ─────────────────────────────────────────────
# HEALTH ENDPOINT
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health():
    """Health endpoint is reachable without authentication."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/health")
    assert r.status_code == 200
    assert r.json()["service"] == "attendance"


# ─────────────────────────────────────────────
# CLASS: TestLeaveValidation
# ─────────────────────────────────────────────

class TestLeaveValidation:
    """Validation logic for leave request creation."""

    @pytest.mark.asyncio
    async def test_from_date_after_to_date_returns_400(self, client_sales):
        """from_date > to_date must be rejected with HTTP 400."""
        r = await client_sales.post("/leaves/", json={
            "leave_type": "casual",
            "from_date": str(date.today() + timedelta(days=5)),
            "to_date": str(date.today()),
            "reason": "Invalid date range",
        })
        assert r.status_code == 400
        assert "from_date must be before to_date" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_same_from_and_to_date_is_valid(self, client_sales):
        """Single-day leave (from_date == to_date) must be accepted."""
        today = str(date.today())
        r = await client_sales.post("/leaves/", json={
            "leave_type": "sick",
            "from_date": today,
            "to_date": today,
            "reason": "Headache",
        })
        assert r.status_code == 201

    @pytest.mark.asyncio
    async def test_multi_day_leave_is_accepted(self, client_sales):
        """A multi-day leave with from_date < to_date must be accepted."""
        r = await client_sales.post("/leaves/", json={
            "leave_type": "earned",
            "from_date": str(date.today()),
            "to_date": str(date.today() + timedelta(days=3)),
            "reason": "Vacation",
        })
        assert r.status_code == 201

    @pytest.mark.asyncio
    async def test_missing_leave_type_returns_422(self, client_sales):
        """Omitting leave_type must return HTTP 422 Unprocessable Entity."""
        r = await client_sales.post("/leaves/", json={
            "from_date": str(date.today()),
            "to_date": str(date.today()),
        })
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_leave_type_returns_422(self, client_sales):
        """An unknown leave_type value must return HTTP 422."""
        r = await client_sales.post("/leaves/", json={
            "leave_type": "galactic",
            "from_date": str(date.today()),
            "to_date": str(date.today()),
        })
        assert r.status_code == 422


# ─────────────────────────────────────────────
# CLASS: TestCheckInCheckOut
# ─────────────────────────────────────────────

class TestCheckInCheckOut:
    """Check-in and check-out endpoint behaviour."""

    @pytest.mark.asyncio
    async def test_check_in_creates_present_record(self, client_sales):
        """Check-in sets status to 'present' and records check_in_at."""
        r = await client_sales.post("/attendance/check-in", json={
            "latitude": 12.9715987,
            "longitude": 77.5945627,
            "notes": "Morning check-in",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "present"
        assert data["check_in_at"] is not None

    @pytest.mark.asyncio
    async def test_check_out_without_check_in_returns_400(self, db):
        """Attempting check-out on a day with no check-in must return 400."""
        fresh_user = _mock_user("sales_rep")

        async def override_db():
            yield db

        async def override_auth():
            return fresh_user

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = override_auth
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/attendance/check-out", json={
                "latitude": 12.97, "longitude": 77.59,
            })
        app.dependency_overrides.clear()
        assert r.status_code == 400
        assert "No check-in found" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_check_in_then_check_out_flow(self, db):
        """Full check-in → check-out flow produces a complete attendance record."""
        user = _mock_user("sales_rep")

        async def override_db():
            yield db

        async def override_auth():
            return user

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = override_auth
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r_in = await c.post("/attendance/check-in", json={
                "latitude": 12.9715987, "longitude": 77.5945627, "notes": "In",
            })
            assert r_in.status_code == 200
            assert r_in.json()["status"] == "present"

            r_out = await c.post("/attendance/check-out", json={
                "latitude": 12.9789, "longitude": 77.6432, "notes": "Out",
            })
            assert r_out.status_code == 200
            assert r_out.json()["check_out_at"] is not None
        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_double_check_in_updates_existing_record(self, db):
        """Checking in twice on the same day upserts (does not create a duplicate)."""
        user = _mock_user("sales_rep")

        async def override_db():
            yield db

        async def override_auth():
            return user

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = override_auth
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r1 = await c.post("/attendance/check-in", json={"notes": "First"})
            r2 = await c.post("/attendance/check-in", json={"notes": "Second"})
            assert r1.status_code == 200
            assert r2.status_code == 200
            # Both responses must reference the same attendance record (same id)
            assert r1.json()["id"] == r2.json()["id"]
        app.dependency_overrides.clear()


# ─────────────────────────────────────────────
# CLASS: TestLeaveFlow
# ─────────────────────────────────────────────

class TestLeaveFlow:
    """End-to-end leave request and approval/rejection flow."""

    @pytest.mark.asyncio
    async def test_create_leave_returns_pending(self, client_sales):
        """Newly created leave must have status 'pending'."""
        r = await client_sales.post("/leaves/", json={
            "leave_type": "sick",
            "from_date": str(date.today()),
            "to_date": str(date.today() + timedelta(days=1)),
            "reason": "Fever",
        })
        assert r.status_code == 201
        assert r.json()["status"] == "pending"

    @pytest.mark.asyncio
    async def test_list_leaves_shows_created_leave(self, client_sales):
        """Created leave must appear in the list endpoint."""
        await client_sales.post("/leaves/", json={
            "leave_type": "casual",
            "from_date": str(date.today()),
            "to_date": str(date.today()),
            "reason": "Personal",
        })
        r = await client_sales.get("/leaves/")
        assert r.status_code == 200
        assert len(r.json()) > 0

    @pytest.mark.asyncio
    async def test_manager_approves_leave(self, db, client_manager):
        """Manager can approve a pending leave request."""
        leave = LeaveRequest(
            tenant_id=MOCK_MANAGER.tenant_id,
            user_id=MOCK_SALES.sub,
            leave_type=LeaveType.casual,
            from_date=date.today(),
            to_date=date.today(),
            reason="Personal errand",
        )
        db.add(leave)
        await db.commit()
        await db.refresh(leave)

        r = await client_manager.patch(f"/leaves/{leave.id}/approve")
        assert r.status_code == 200
        assert r.json()["status"] == "approved"

    @pytest.mark.asyncio
    async def test_manager_rejects_leave(self, db, client_manager):
        """Manager can reject a pending leave request."""
        leave = LeaveRequest(
            tenant_id=MOCK_MANAGER.tenant_id,
            user_id=MOCK_SALES.sub,
            leave_type=LeaveType.sick,
            from_date=date.today(),
            to_date=date.today(),
            reason="Cold",
        )
        db.add(leave)
        await db.commit()
        await db.refresh(leave)

        r = await client_manager.patch(f"/leaves/{leave.id}/reject")
        assert r.status_code == 200
        assert r.json()["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_sales_rep_cannot_approve_leave(self, db, client_sales):
        """Sales rep must receive 403 when trying to approve a leave."""
        leave = LeaveRequest(
            tenant_id=MOCK_SALES.tenant_id,
            user_id=MOCK_SALES.sub,
            leave_type=LeaveType.earned,
            from_date=date.today(),
            to_date=date.today(),
            reason="Day off",
        )
        db.add(leave)
        await db.commit()
        await db.refresh(leave)

        r = await client_sales.patch(f"/leaves/{leave.id}/approve")
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_approve_nonexistent_leave_returns_404(self, client_manager):
        """Approving a non-existent leave ID must return 404."""
        r = await client_manager.patch(f"/leaves/{uuid.uuid4()}/approve")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_list_leaves_filter_by_status(self, client_sales):
        """List endpoint filters correctly by status query parameter."""
        r = await client_sales.get(f"/leaves/?user_id={MOCK_SALES.sub}&status=approved")
        assert r.status_code == 200
        # All returned leaves must have the requested status
        for leave in r.json():
            assert leave["status"] == "approved"


# ─────────────────────────────────────────────
# CLASS: TestAvailabilityCheck
# ─────────────────────────────────────────────

class TestAvailabilityCheck:
    """Availability endpoint used by Orchestration service."""

    @pytest.mark.asyncio
    async def test_unknown_user_is_available(self, client_sales):
        """A user with no attendance or leave record is considered available."""
        r = await client_sales.get(f"/attendance/availability/{uuid.uuid4()}")
        assert r.status_code == 200
        data = r.json()
        assert data["is_available"] is True
        assert data["reason"] == "Available"

    @pytest.mark.asyncio
    async def test_user_on_approved_leave_is_unavailable(self, db, client_sales):
        """A user with an approved leave covering today is marked unavailable."""
        target_user = str(uuid.uuid4())
        leave = LeaveRequest(
            tenant_id=MOCK_SALES.tenant_id,
            user_id=target_user,
            leave_type=LeaveType.casual,
            from_date=date.today(),
            to_date=date.today(),
            reason="Day off",
            status=LeaveStatus.approved,
        )
        db.add(leave)
        await db.commit()

        r = await client_sales.get(f"/attendance/availability/{target_user}")
        assert r.status_code == 200
        data = r.json()
        assert data["is_available"] is False
        assert data["status"] == "on_leave"

    @pytest.mark.asyncio
    async def test_absent_user_is_unavailable(self, db, client_sales):
        """A user marked absent for today is unavailable."""
        target_user = str(uuid.uuid4())
        attendance = Attendance(
            tenant_id=MOCK_SALES.tenant_id,
            user_id=target_user,
            attendance_date=date.today(),
            status=AttendanceStatus.absent,
        )
        db.add(attendance)
        await db.commit()

        r = await client_sales.get(f"/attendance/availability/{target_user}")
        assert r.status_code == 200
        data = r.json()
        assert data["is_available"] is False
        assert data["status"] == "absent"

    @pytest.mark.asyncio
    async def test_present_user_is_available(self, db, client_sales):
        """A user with a 'present' attendance record is still available."""
        target_user = str(uuid.uuid4())
        attendance = Attendance(
            tenant_id=MOCK_SALES.tenant_id,
            user_id=target_user,
            attendance_date=date.today(),
            status=AttendanceStatus.present,
            check_in_at=datetime.now(),
        )
        db.add(attendance)
        await db.commit()

        r = await client_sales.get(f"/attendance/availability/{target_user}")
        assert r.status_code == 200
        data = r.json()
        assert data["is_available"] is True

    @pytest.mark.asyncio
    async def test_availability_response_has_required_fields(self, client_sales):
        """Availability response must include user_id, check_date, is_available, reason."""
        r = await client_sales.get(f"/attendance/availability/{uuid.uuid4()}")
        assert r.status_code == 200
        data = r.json()
        assert "user_id" in data
        assert "check_date" in data
        assert "is_available" in data
        assert "reason" in data


# ─────────────────────────────────────────────
# CLASS: TestAttendanceListing
# ─────────────────────────────────────────────

class TestAttendanceListing:
    """List attendance endpoint filtering tests."""

    @pytest.mark.asyncio
    async def test_list_attendance_returns_list(self, client_sales):
        """List attendance endpoint returns a JSON array."""
        r = await client_sales.get("/attendance/")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    @pytest.mark.asyncio
    async def test_list_attendance_date_filter(self, client_sales):
        """Date range filter passes through without error."""
        from_d = str(date.today() - timedelta(days=7))
        to_d = str(date.today())
        r = await client_sales.get(f"/attendance/?from_date={from_d}&to_date={to_d}")
        assert r.status_code == 200
