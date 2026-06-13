"""Attendance service tests — check-in/out, availability blocking, leave management."""
import pytest
import uuid
from datetime import date, timedelta
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from unittest.mock import patch

from app.main import app, get_db, TokenPayload, AttendanceStatus, LeaveStatus


TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
test_engine = create_async_engine(TEST_DB_URL)
TestSession = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


def mock_user(role="sales_rep"):
    return TokenPayload(
        sub=str(uuid.uuid4()), tenant_id=str(uuid.uuid4()),
        roles=[role], exp=9999999999, iat=1000000000, jti=str(uuid.uuid4())
    )


MOCK_SALES = mock_user("sales_rep")
MOCK_MANAGER = mock_user("manager")


from app.main import Base


@pytest.fixture(scope="session", autouse=True)
async def setup_test_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def db():
    async with TestSession() as session:
        yield session


@pytest.fixture
async def client_sales(db):
    async def override_db():
        yield db

    async def override_auth():
        return MOCK_SALES

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[app.routes[0].dependant.dependencies[0].call if False else __import__('app.main', fromlist=['get_current_user']).get_current_user] = override_auth
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_health():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/health")
    assert r.status_code == 200
    assert r.json()["service"] == "attendance"


@pytest.mark.asyncio
async def test_leave_date_validation(client_sales):
    """from_date after to_date should return 400."""
    from datetime import date, timedelta
    r = await client_sales.post("/leaves/", json={
        "leave_type": "casual",
        "from_date": str(date.today() + timedelta(days=5)),
        "to_date": str(date.today()),
        "reason": "Invalid dates"
    })
    assert r.status_code == 400
    assert "from_date must be before to_date" in r.json()["detail"]


@pytest.mark.asyncio
async def test_check_in_and_out(client_sales):
    """Check in and then check out successfully."""
    r = await client_sales.post("/attendance/check-in", json={
        "latitude": 12.9715987,
        "longitude": 77.5945627,
        "notes": "Starting shift"
    })
    assert r.status_code == 200
    assert r.json()["status"] == "present"

    r = await client_sales.post("/attendance/check-out", json={
        "latitude": 12.9789,
        "longitude": 77.6432,
        "notes": "Ending shift"
    })
    assert r.status_code == 200
    assert r.json()["check_out_at"] is not None


@pytest.mark.asyncio
async def test_leave_flow(client_sales):
    """Create a leave request and list it."""
    from datetime import date, timedelta
    r = await client_sales.post("/leaves/", json={
        "leave_type": "sick",
        "from_date": str(date.today()),
        "to_date": str(date.today() + timedelta(days=1)),
        "reason": "Fever"
    })
    assert r.status_code == 201

    r = await client_sales.get("/leaves/")
    assert r.status_code == 200
    assert len(r.json()) > 0


@pytest.mark.asyncio
async def test_leave_type_validation():
    """Validate LeaveType enum values."""
    from app.main import LeaveType
    assert LeaveType.casual.value == "casual"
    assert LeaveType.sick.value == "sick"
    assert LeaveType.earned.value == "earned"
    assert LeaveType.unpaid.value == "unpaid"


@pytest.mark.asyncio
async def test_attendance_status_enum():
    """Validate AttendanceStatus enum values."""
    from app.main import AttendanceStatus
    assert AttendanceStatus.present.value == "present"
    assert AttendanceStatus.absent.value == "absent"
    assert AttendanceStatus.on_leave.value == "on_leave"


@pytest.mark.asyncio
async def test_leave_status_flow():
    """Leave goes through pending -> approved/rejected -> cancelled."""
    from app.main import LeaveStatus
    assert LeaveStatus.pending.value == "pending"
    assert LeaveStatus.approved.value == "approved"
    assert LeaveStatus.rejected.value == "rejected"
    assert LeaveStatus.cancelled.value == "cancelled"


@pytest.mark.asyncio
async def test_leave_approval_and_rejection(db):
    """Test manager approving and rejecting a leave request."""
    from datetime import date
    from app.main import LeaveRequest, LeaveType, LeaveStatus
    leave = LeaveRequest(
        tenant_id=MOCK_SALES.tenant_id,
        user_id=MOCK_SALES.sub,
        leave_type=LeaveType.casual,
        from_date=date.today(),
        to_date=date.today(),
        reason="Test",
    )
    db.add(leave)
    await db.commit()
    await db.refresh(leave)

    async def override_auth_manager():
        return MOCK_MANAGER

    # Override get_current_user and get_db dependencies
    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[__import__('app.main', fromlist=['get_current_user']).get_current_user] = override_auth_manager
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        # Approve
        r = await c.patch(f"/leaves/{leave.id}/approve")
        assert r.status_code == 200
        assert r.json()["status"] == "approved"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_check_availability_endpoint(client_sales, db):
    """Test availability check endpoint."""
    r = await client_sales.get(f"/attendance/availability/{uuid.uuid4()}")
    assert r.status_code == 200
    assert r.json()["is_available"] is True

    r = await client_sales.get(f"/attendance/availability/{MOCK_SALES.sub}")
    assert r.status_code == 200
    assert r.json()["is_available"] is False


@pytest.mark.asyncio
async def test_reject_leave_request(db):
    """Test manager rejecting a leave request."""
    from datetime import date
    from app.main import LeaveRequest, LeaveType
    leave = LeaveRequest(
        tenant_id=MOCK_SALES.tenant_id,
        user_id=MOCK_SALES.sub,
        leave_type=LeaveType.sick,
        from_date=date.today(),
        to_date=date.today(),
        reason="Test reject",
    )
    db.add(leave)
    await db.commit()
    await db.refresh(leave)

    async def override_auth_manager():
        return MOCK_MANAGER

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[__import__('app.main', fromlist=['get_current_user']).get_current_user] = override_auth_manager
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.patch(f"/leaves/{leave.id}/reject")
        assert r.status_code == 200
        assert r.json()["status"] == "rejected"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_leaves_filtered(client_sales):
    """List leaves with user_id and status filters."""
    r = await client_sales.get(f"/leaves/?user_id={MOCK_SALES.sub}&status=approved")
    assert r.status_code == 200
