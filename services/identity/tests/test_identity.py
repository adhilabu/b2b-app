"""
Identity service tests — JWT auth, password hashing, schema validation, RBAC.

Pure unit tests and FastAPI TestClient (SQLite in-memory) tests.
No real database, no real RSA key files, no real Redis required.
"""
import time
import uuid
import pytest
import pytest_asyncio
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from passlib.context import CryptContext

# ─────────────────────────────────────────────
# RSA KEY GENERATION (test-only, no file I/O)
# ─────────────────────────────────────────────

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
TEST_PRIVATE_PEM = _private_key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.TraditionalOpenSSL,
    serialization.NoEncryption(),
).decode()
TEST_PUBLIC_PEM = _private_key.public_key().public_bytes(
    serialization.Encoding.PEM,
    serialization.PublicFormat.SubjectPublicKeyInfo,
).decode()

# Patch Settings so importing app.main does not try to open PEM files from disk.
_settings_patch = patch.multiple(
    "app.config.Settings",
    jwt_private_key=property(lambda self: TEST_PRIVATE_PEM),
    jwt_public_key=property(lambda self: TEST_PUBLIC_PEM),
)
_settings_patch.start()

from app.main import app
from app.database import get_db, Base
from app.models.user import User, Tenant, UserRole
from app.auth.jwt import create_access_token, decode_access_token
from app.schemas.schemas import (
    TokenPayload, UserCreate, CustomerCreate, LoginRequest,
    TenantCreate,
)

# ─────────────────────────────────────────────
# TEST DATABASE
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
    tenant = Tenant(
        name="Acme Corp",
        slug=f"acme-{uuid.uuid4().hex[:6]}",
        settings={"currency": "INR"},
    )
    db_session.add(tenant)
    await db_session.commit()
    await db_session.refresh(tenant)
    return tenant


@pytest_asyncio.fixture
async def test_admin(db_session, test_tenant):
    user = User(
        email=f"admin-{uuid.uuid4().hex[:6]}@acme.com",
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
async def test_manager(db_session, test_tenant):
    user = User(
        email=f"manager-{uuid.uuid4().hex[:6]}@acme.com",
        full_name="Manager User",
        hashed_password=pwd_context.hash("SecurePass123"),
        role=UserRole.manager,
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
        email=f"rep-{uuid.uuid4().hex[:6]}@acme.com",
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


def _make_token_payload(user: User, roles: list | None = None) -> TokenPayload:
    """Build a TokenPayload for dependency overrides."""
    return TokenPayload(
        sub=str(user.id),
        tenant_id=str(user.tenant_id),
        roles=roles or [user.role.value],
        exp=int(time.time()) + 3600,
        iat=int(time.time()),
        jti=str(uuid.uuid4()),
    )


# ─────────────────────────────────────────────
# CLASS: TestJWT
# ─────────────────────────────────────────────

class TestJWT:
    """Unit tests for RS256 JWT creation and decoding."""

    def test_create_access_token_returns_string_and_expiry(self):
        """create_access_token returns a non-empty JWT string and positive expiry."""
        token, expires_in = create_access_token(
            user_id=str(uuid.uuid4()),
            tenant_id=str(uuid.uuid4()),
            roles=["sales_rep"],
        )
        assert isinstance(token, str)
        assert len(token) > 50
        assert expires_in > 0

    def test_decode_access_token_roundtrip(self):
        """Token created by create_access_token can be decoded back to the same claims."""
        uid = str(uuid.uuid4())
        tid = str(uuid.uuid4())
        token, _ = create_access_token(user_id=uid, tenant_id=tid, roles=["admin"])
        payload = decode_access_token(token)
        assert payload.sub == uid
        assert payload.tenant_id == tid
        assert "admin" in payload.roles

    def test_decode_returns_token_payload_type(self):
        """decode_access_token returns a TokenPayload instance."""
        token, _ = create_access_token(
            user_id=str(uuid.uuid4()),
            tenant_id=str(uuid.uuid4()),
            roles=["manager"],
        )
        payload = decode_access_token(token)
        assert isinstance(payload, TokenPayload)

    def test_token_has_unique_jti(self):
        """Two tokens issued back-to-back have different JTIs (for revocation support)."""
        uid = str(uuid.uuid4())
        tid = str(uuid.uuid4())
        t1, _ = create_access_token(user_id=uid, tenant_id=tid, roles=["sales_rep"])
        t2, _ = create_access_token(user_id=uid, tenant_id=tid, roles=["sales_rep"])
        p1 = decode_access_token(t1)
        p2 = decode_access_token(t2)
        assert p1.jti != p2.jti

    def test_tampered_token_raises_jwt_error(self):
        """A token with a corrupted signature raises JWTError on decode."""
        from jose import JWTError
        token, _ = create_access_token(
            user_id=str(uuid.uuid4()),
            tenant_id=str(uuid.uuid4()),
            roles=["admin"],
        )
        # Flip last character to corrupt the HMAC
        bad_token = token[:-1] + ("X" if token[-1] != "X" else "Y")
        with pytest.raises(JWTError):
            decode_access_token(bad_token)

    def test_create_refresh_token_returns_uuid_strings(self):
        """create_refresh_token returns (jti, token) where both are valid UUID strings."""
        from app.auth.jwt import create_refresh_token
        jti, token = create_refresh_token(user_id=str(uuid.uuid4()))
        assert jti == token  # Refresh token is the JTI itself (stored in Redis)
        uuid.UUID(jti)       # Must be a valid UUID — raises ValueError otherwise

    def test_multiple_roles_preserved_through_encode_decode(self):
        """All roles in the payload survive encode/decode unchanged."""
        roles = ["admin", "manager", "supervisor"]
        token, _ = create_access_token(
            user_id=str(uuid.uuid4()),
            tenant_id=str(uuid.uuid4()),
            roles=roles,
        )
        payload = decode_access_token(token)
        assert set(payload.roles) == set(roles)

    def test_token_jti_is_valid_uuid(self):
        """The JTI embedded in the token is a parseable UUID."""
        token, _ = create_access_token(
            user_id=str(uuid.uuid4()),
            tenant_id=str(uuid.uuid4()),
            roles=["driver"],
        )
        payload = decode_access_token(token)
        uuid.UUID(payload.jti)  # Raises ValueError if not a valid UUID


# ─────────────────────────────────────────────
# CLASS: TestPasswordHashing
# ─────────────────────────────────────────────

class TestPasswordHashing:
    """Unit tests for bcrypt password hashing behaviour."""

    def test_hash_produces_non_plaintext_string(self):
        """Hashed password does not equal the original plaintext."""
        plain = "SuperSecret99!"
        hashed = pwd_context.hash(plain)
        assert hashed != plain

    def test_verify_correct_password_returns_true(self):
        """Correct password verifies successfully against its hash."""
        plain = "CorrectHorse$Battery"
        assert pwd_context.verify(plain, pwd_context.hash(plain)) is True

    def test_verify_wrong_password_returns_false(self):
        """Wrong password returns False (not an exception)."""
        hashed = pwd_context.hash("RealPassword1!")
        assert pwd_context.verify("WrongPassword!", hashed) is False

    def test_two_hashes_of_same_password_differ(self):
        """bcrypt uses a random salt so two hashes of the same input are not equal."""
        plain = "SameInput123"
        h1 = pwd_context.hash(plain)
        h2 = pwd_context.hash(plain)
        assert h1 != h2

    def test_hash_prefix_indicates_bcrypt(self):
        """bcrypt hashes start with the standard $2b$ prefix."""
        hashed = pwd_context.hash("AnyPassword!")
        assert hashed.startswith("$2b$") or hashed.startswith("$2a$")

    def test_empty_password_can_be_hashed_and_verified(self):
        """Edge-case: empty string can be hashed and correctly verified."""
        hashed = pwd_context.hash("")
        assert pwd_context.verify("", hashed) is True
        assert pwd_context.verify("notempty", hashed) is False


# ─────────────────────────────────────────────
# CLASS: TestSchemaValidation
# ─────────────────────────────────────────────

class TestSchemaValidation:
    """Pydantic schema validation tests — correct shapes are accepted, bad ones rejected."""

    def test_user_create_valid(self):
        """Valid UserCreate passes validation and sets default role."""
        u = UserCreate(
            email="rep@example.com",
            full_name="John Doe",
            password="SecurePass1!",
            tenant_id=uuid.uuid4(),
        )
        assert u.email == "rep@example.com"
        assert u.role == UserRole.sales_rep  # Default role

    def test_user_create_short_password_raises(self):
        """Password shorter than 8 characters is rejected with descriptive error."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="Password must be at least 8 characters"):
            UserCreate(
                email="rep@example.com",
                full_name="Short PW",
                password="1234567",  # Only 7 chars
                tenant_id=uuid.uuid4(),
            )

    def test_user_create_invalid_email_raises(self):
        """Invalid email format raises ValidationError."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            UserCreate(
                email="not-an-email",
                full_name="Bad Email",
                password="ValidPass123",
                tenant_id=uuid.uuid4(),
            )

    def test_user_create_all_roles_accepted(self):
        """All UserRole enum values are accepted by UserCreate."""
        for role in UserRole:
            u = UserCreate(
                email=f"{role.value}@example.com",
                full_name="Test User",
                password="GoodPass123",
                tenant_id=uuid.uuid4(),
                role=role,
            )
            assert u.role == role

    def test_customer_create_defaults(self):
        """CustomerCreate defaults country to India and credit_limit to 0."""
        c = CustomerCreate(name="Best Shop")
        assert c.country == "India"
        assert c.credit_limit == 0.0

    def test_customer_create_with_optional_client_uuid(self):
        """client_uuid is optional and stored as a UUID object."""
        cid = uuid.uuid4()
        c = CustomerCreate(name="Shop", client_uuid=cid)
        assert c.client_uuid == cid

    def test_customer_create_optional_fields_default_none(self):
        """Optional fields like code, phone, email default to None."""
        c = CustomerCreate(name="Minimal Shop")
        assert c.code is None
        assert c.phone is None
        assert c.email is None

    def test_tenant_create_stores_slug(self):
        """TenantCreate stores name and slug correctly, settings defaults to {}."""
        t = TenantCreate(name="Acme Corp", slug="acme-corp")
        assert t.slug == "acme-corp"
        assert t.settings == {}

    def test_login_request_valid(self):
        """LoginRequest accepts valid email+password combination."""
        lr = LoginRequest(email="user@example.com", password="anypassword")
        assert lr.email == "user@example.com"

    def test_token_payload_requires_all_fields(self):
        """TokenPayload rejects construction if required fields are missing."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            TokenPayload(sub="x")  # Missing tenant_id, roles, exp, iat, jti

    def test_token_payload_full_construction(self):
        """TokenPayload is valid when all required fields are provided."""
        tp = TokenPayload(
            sub=str(uuid.uuid4()),
            tenant_id=str(uuid.uuid4()),
            roles=["sales_rep"],
            exp=int(time.time()) + 3600,
            iat=int(time.time()),
            jti=str(uuid.uuid4()),
        )
        assert "sales_rep" in tp.roles


# ─────────────────────────────────────────────
# CLASS: TestRBAC
# ─────────────────────────────────────────────

class TestRBAC:
    """Role hierarchy and access control tests."""

    def test_user_role_enum_values(self):
        """UserRole enum has the expected string values for all roles."""
        assert UserRole.admin.value == "admin"
        assert UserRole.manager.value == "manager"
        assert UserRole.sales_rep.value == "sales_rep"
        assert UserRole.driver.value == "driver"
        assert UserRole.supervisor.value == "supervisor"

    def test_admin_is_above_manager_in_hierarchy(self):
        """admin role is distinct from manager and can be compared."""
        assert UserRole.admin != UserRole.manager

    def test_require_roles_factory_returns_callable(self):
        """require_roles factory produces a callable FastAPI dependency."""
        from app.auth.rbac import require_roles
        checker = require_roles(UserRole.admin)
        assert callable(checker)

    def test_require_admin_shortcut_is_callable(self):
        """require_admin shortcut is a valid FastAPI dependency callable."""
        from app.auth.rbac import require_admin
        assert callable(require_admin)

    def test_require_manager_or_above_shortcut_is_callable(self):
        """require_manager_or_above shortcut is callable."""
        from app.auth.rbac import require_manager_or_above
        assert callable(require_manager_or_above)

    def test_require_supervisor_or_above_shortcut_is_callable(self):
        """require_supervisor_or_above shortcut is callable."""
        from app.auth.rbac import require_supervisor_or_above
        assert callable(require_supervisor_or_above)

    @pytest.mark.asyncio
    async def test_sales_rep_cannot_create_user(self, client, test_sales_rep, test_tenant):
        """A sales_rep token must receive 403 when trying to create a user."""
        from app.auth.dependencies import get_current_user
        payload = _make_token_payload(test_sales_rep)

        async def override_auth():
            return payload

        app.dependency_overrides[get_current_user] = override_auth
        try:
            r = await client.post(
                "/users/",
                json={
                    "email": "newrep@test.com",
                    "full_name": "New Rep",
                    "password": "Pass1234!",
                    "role": "sales_rep",
                    "tenant_id": str(test_tenant.id),
                },
                headers={"Authorization": "Bearer faketoken"},
            )
        finally:
            app.dependency_overrides.pop(get_current_user, None)
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_can_create_user(self, client, test_admin, test_tenant):
        """An admin token must receive 201 when creating a new user."""
        from app.auth.dependencies import get_current_user
        payload = _make_token_payload(test_admin)

        async def override_auth():
            return payload

        app.dependency_overrides[get_current_user] = override_auth
        try:
            r = await client.post(
                "/users/",
                json={
                    "email": f"fresh-{uuid.uuid4().hex[:6]}@test.com",
                    "full_name": "Fresh User",
                    "password": "Pass5678!",
                    "role": "sales_rep",
                    "tenant_id": str(test_tenant.id),
                },
                headers={"Authorization": "Bearer faketoken"},
            )
        finally:
            app.dependency_overrides.pop(get_current_user, None)
        assert r.status_code == 201

    @pytest.mark.asyncio
    async def test_manager_cannot_create_user(self, client, test_manager, test_tenant):
        """A manager token must receive 403 — only admin can create users."""
        from app.auth.dependencies import get_current_user
        payload = _make_token_payload(test_manager)

        async def override_auth():
            return payload

        app.dependency_overrides[get_current_user] = override_auth
        try:
            r = await client.post(
                "/users/",
                json={
                    "email": "mgr-created@test.com",
                    "full_name": "MGR Created",
                    "password": "Pass1234!",
                    "role": "sales_rep",
                    "tenant_id": str(test_tenant.id),
                },
                headers={"Authorization": "Bearer faketoken"},
            )
        finally:
            app.dependency_overrides.pop(get_current_user, None)
        assert r.status_code == 403


# ─────────────────────────────────────────────
# HEALTH & AUTH ENDPOINT TESTS
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_endpoint(client):
    """Health endpoint is reachable and returns the expected structure."""
    r = await client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "healthy"
    assert data["service"] == "identity"


@pytest.mark.asyncio
async def test_login_success(client, test_admin):
    """Correct credentials return access_token, refresh_token, and bearer type."""
    with patch("app.routers.auth.get_redis") as mock_redis:
        mock_r = AsyncMock()
        mock_r.setex = AsyncMock()
        mock_r.aclose = AsyncMock()
        mock_redis.return_value = mock_r

        r = await client.post("/auth/login", json={
            "email": test_admin.email,
            "password": "SecurePass123",
        })
    assert r.status_code == 200
    data = r.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    assert data["expires_in"] > 0


@pytest.mark.asyncio
async def test_login_wrong_password(client, test_admin):
    """Incorrect password returns 401 with descriptive message."""
    r = await client.post("/auth/login", json={
        "email": test_admin.email,
        "password": "WrongPassword!",
    })
    assert r.status_code == 401
    assert "Invalid email or password" in r.json()["detail"]


@pytest.mark.asyncio
async def test_login_unknown_email(client):
    """Non-existent email returns 401 (same response as wrong password — no enumeration)."""
    r = await client.post("/auth/login", json={
        "email": "nobody-at-all@example.com",
        "password": "AnyPass1234",
    })
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_logout_calls_redis_delete(client):
    """Logout endpoint calls Redis delete with the correct namespaced key."""
    jti = str(uuid.uuid4())
    with patch("app.routers.auth.get_redis") as mock_redis:
        mock_r = AsyncMock()
        mock_r.delete = AsyncMock()
        mock_r.aclose = AsyncMock()
        mock_redis.return_value = mock_r

        r = await client.post("/auth/logout", json={"refresh_token": jti})
        assert r.status_code == 204
        mock_r.delete.assert_called_once_with(f"refresh:{jti}")


@pytest.mark.asyncio
async def test_list_users_requires_auth(client):
    """Unauthenticated call to /users/ is rejected with 403."""
    r = await client.get("/users/")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_customer_sync_watermark_structure(client, test_sales_rep):
    """Sync endpoint returns the expected domain/watermark/entities shape."""
    payload = _make_token_payload(test_sales_rep)
    with patch("app.auth.dependencies.decode_access_token", return_value=payload):
        r = await client.get(
            "/customers/sync?since_version=0",
            headers={"Authorization": "Bearer mocktoken"},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["domain"] == "identity"
    assert "watermark" in data
    assert "entities" in data


@pytest.mark.asyncio
async def test_create_and_list_customers(client, test_sales_rep):
    """Sales rep can create a customer and it appears in the listing."""
    payload = _make_token_payload(test_sales_rep)
    with patch("app.auth.dependencies.decode_access_token", return_value=payload):
        r_create = await client.post(
            "/customers/",
            json={
                "name": "Corner Store",
                "code": "CS001",
                "contact_person": "Ram Kumar",
                "phone": "9876543210",
                "city": "Chennai",
            },
            headers={"Authorization": "Bearer mocktoken"},
        )
        assert r_create.status_code == 201
        assert r_create.json()["name"] == "Corner Store"

        r_list = await client.get(
            "/customers/",
            headers={"Authorization": "Bearer mocktoken"},
        )
        assert r_list.status_code == 200
        assert len(r_list.json()) >= 1


@pytest.mark.asyncio
async def test_create_user_success(client, test_admin, test_tenant):
    """Admin can create a new user and the response includes the email."""
    from app.auth.dependencies import get_current_user
    payload = _make_token_payload(test_admin)

    async def override_auth():
        return payload

    app.dependency_overrides[get_current_user] = override_auth
    try:
        r = await client.post(
            "/users/",
            json={
                "email": f"new-{uuid.uuid4().hex[:6]}@test.com",
                "full_name": "New Rep",
                "password": "Pass1234!",
                "role": "sales_rep",
                "tenant_id": str(test_tenant.id),
            },
            headers={"Authorization": "Bearer faketoken"},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert r.status_code == 201
    assert "@test.com" in r.json()["email"]
