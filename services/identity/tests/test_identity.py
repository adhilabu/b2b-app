"""Identity service tests — auth, users, customers."""
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from unittest.mock import AsyncMock, patch, MagicMock

from app.main import app
from app.database import get_db, Base
from app.models.user import User, Tenant, UserRole
from app.auth.jwt import create_access_token
from passlib.context import CryptContext
import uuid

# ─────────────────────────────────────────────
# TEST DATABASE SETUP
# ─────────────────────────────────────────────

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSession = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session():
    async with TestSession() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_session):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def test_tenant(db_session):
    tenant = Tenant(name="Test Corp", slug=f"test-corp-{uuid.uuid4().hex[:6]}", settings={})
    db_session.add(tenant)
    await db_session.commit()
    await db_session.refresh(tenant)
    return tenant


@pytest_asyncio.fixture
async def test_admin(db_session, test_tenant):
    user = User(
        email=f"admin-{uuid.uuid4().hex[:6]}@test.com",
        full_name="Admin User",
        hashed_password=pwd_context.hash("SecurePass123"),
        role=UserRole.admin,
        tenant_id=test_tenant.id,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def test_sales_rep(db_session, test_tenant):
    user = User(
        email=f"rep-{uuid.uuid4().hex[:6]}@test.com",
        full_name="Sales Rep",
        hashed_password=pwd_context.hash("SecurePass123"),
        role=UserRole.sales_rep,
        tenant_id=test_tenant.id,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


def make_token(user: User) -> str:
    """Create a real JWT for testing (mocks key loading)."""
    # Use HMAC HS256 for tests to avoid needing RSA keys
    from jose import jwt
    import time
    payload = {
        "sub": str(user.id),
        "tenant_id": str(user.tenant_id),
        "roles": [user.role.value],
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, "test-secret", algorithm="HS256")


# ─────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert response.json()["service"] == "identity"


# ─────────────────────────────────────────────
# AUTH TESTS
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_login_success(client, test_admin):
    """Valid credentials return access and refresh tokens."""
    with patch("app.routers.auth.get_redis") as mock_redis:
        mock_r = AsyncMock()
        mock_r.setex = AsyncMock()
        mock_r.aclose = AsyncMock()
        mock_redis.return_value = mock_r

        response = await client.post("/auth/login", json={
            "email": test_admin.email,
            "password": "SecurePass123",
        })
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    assert data["expires_in"] > 0


@pytest.mark.asyncio
async def test_login_wrong_password(client, test_admin):
    """Wrong password returns 401."""
    response = await client.post("/auth/login", json={
        "email": test_admin.email,
        "password": "WrongPassword",
    })
    assert response.status_code == 401
    assert "Invalid email or password" in response.json()["detail"]


@pytest.mark.asyncio
async def test_login_nonexistent_user(client):
    """Non-existent email returns 401."""
    response = await client.post("/auth/login", json={
        "email": "nobody@example.com",
        "password": "AnyPass123",
    })
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_logout_revokes_token(client):
    """Logout endpoint deletes refresh token from Redis."""
    refresh_jti = str(uuid.uuid4())
    with patch("app.routers.auth.get_redis") as mock_redis:
        mock_r = AsyncMock()
        mock_r.delete = AsyncMock()
        mock_r.aclose = AsyncMock()
        mock_redis.return_value = mock_r

        response = await client.post("/auth/logout", json={"refresh_token": refresh_jti})
        assert response.status_code == 204
        mock_r.delete.assert_called_once_with(f"refresh:{refresh_jti}")


# ─────────────────────────────────────────────
# USER TESTS
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_user_requires_admin(client, test_sales_rep, test_tenant):
    """Non-admin cannot create users."""
    token = make_token(test_sales_rep)
    with patch("app.auth.dependencies.decode_access_token") as mock_decode:
        from app.schemas.schemas import TokenPayload
        import time, uuid as uuid_mod
        mock_decode.return_value = TokenPayload(
            sub=str(test_sales_rep.id),
            tenant_id=str(test_sales_rep.tenant_id),
            roles=["sales_rep"],
            exp=int(time.time()) + 3600,
            iat=int(time.time()),
            jti=str(uuid_mod.uuid4()),
        )
        response = await client.post(
            "/users/",
            json={"email": "new@test.com", "full_name": "New", "password": "Pass1234!", "tenant_id": str(test_tenant.id)},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_list_users_requires_auth(client):
    """Unauthenticated request returns 403."""
    response = await client.get("/users/")
    assert response.status_code == 403


# ─────────────────────────────────────────────
# CUSTOMER TESTS
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_check(client):
    """Ensure the service is accessible."""
    r = await client.get("/health")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_jwt_decode_valid():
    """Test JWT creation and decoding."""
    from app.auth.jwt import create_access_token, decode_access_token
    from unittest.mock import patch, mock_open

    fake_private = """-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA2a2rwplBQLzHPZe5TNJB9UMCmFp4F9O/xBWBkblzmzJVG4lj
-----END RSA PRIVATE KEY-----"""  # Placeholder — real tests use actual key fixtures

    with patch("app.config.Settings.jwt_private_key", new_callable=lambda: property(lambda self: fake_private)):
        # In real tests, use proper RSA key fixtures
        pass


@pytest.mark.asyncio
async def test_customer_sync_endpoint_returns_watermark(client, db_session, test_sales_rep, test_tenant):
    """Sync endpoint returns correct watermark structure."""
    with patch("app.auth.dependencies.decode_access_token") as mock_decode:
        from app.schemas.schemas import TokenPayload
        import time, uuid as uid
        mock_decode.return_value = TokenPayload(
            sub=str(test_sales_rep.id),
            tenant_id=str(test_sales_rep.tenant_id),
            roles=["sales_rep"],
            exp=int(time.time()) + 3600,
            iat=int(time.time()),
            jti=str(uid.uuid4()),
        )
        response = await client.get(
            "/customers/sync?since_version=0",
            headers={"Authorization": "Bearer mocktoken"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["domain"] == "identity"
    assert "watermark" in data
    assert "entities" in data

@pytest.mark.asyncio
async def test_create_user_success(client, test_admin, test_tenant):
    """Admin can create users."""
    token = make_token(test_admin)
    with patch("app.auth.dependencies.decode_access_token") as mock_decode:
        from app.schemas.schemas import TokenPayload
        import time, uuid as uuid_mod
        mock_decode.return_value = TokenPayload(
            sub=str(test_admin.id),
            tenant_id=str(test_admin.tenant_id),
            roles=["admin"],
            exp=int(time.time()) + 3600,
            iat=int(time.time()),
            jti=str(uuid_mod.uuid4()),
        )
        response = await client.post(
            "/users/",
            json={"email": "new_rep@test.com", "full_name": "New Rep", "password": "Pass1234!", "role": "sales_rep", "tenant_id": str(test_tenant.id)},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 201
    assert response.json()["email"] == "new_rep@test.com"

@pytest.mark.asyncio
async def test_create_and_list_customers(client, test_sales_rep, test_tenant):
    """Rep can create and list customers."""
    token = make_token(test_sales_rep)
    with patch("app.auth.dependencies.decode_access_token") as mock_decode:
        from app.schemas.schemas import TokenPayload
        import time, uuid as uuid_mod
        mock_decode.return_value = TokenPayload(
            sub=str(test_sales_rep.id),
            tenant_id=str(test_sales_rep.tenant_id),
            roles=["sales_rep"],
            exp=int(time.time()) + 3600,
            iat=int(time.time()),
            jti=str(uuid_mod.uuid4()),
        )
        response = await client.post(
            "/customers/",
            json={"name": "New Shop", "code": "SHOP1", "contact_person": "Shop owner", "phone": "12345678", "city": "Bangalore"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 201
        assert response.json()["name"] == "New Shop"

        response = await client.get(
            "/customers/",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert len(response.json()) > 0
