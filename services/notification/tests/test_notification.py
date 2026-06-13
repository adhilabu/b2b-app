"""Notification service tests — WebSocket manager, dispatch stubs, VAPID endpoint."""
import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from app.main import ConnectionManager, FCMHandler, WebPushHandler, EmailHandler


class TestConnectionManager:
    @pytest.mark.asyncio
    async def test_connect_registers_connection(self):
        manager = ConnectionManager()
        ws = AsyncMock()
        await manager.connect("user-1", ws)
        assert "user-1" in manager._connections
        assert ws in manager._connections["user-1"]
        ws.accept.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_to_user_delivers_message(self):
        manager = ConnectionManager()
        ws = AsyncMock()
        await manager.connect("user-1", ws)
        count = await manager.send_to_user("user-1", {"type": "test", "body": "hello"})
        assert count == 1
        ws.send_json.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_to_unknown_user_returns_zero(self):
        manager = ConnectionManager()
        count = await manager.send_to_user("unknown-user", {"type": "test"})
        assert count == 0

    @pytest.mark.asyncio
    async def test_dead_connections_cleaned_up(self):
        manager = ConnectionManager()
        ws = AsyncMock()
        ws.send_json.side_effect = Exception("Connection closed")
        await manager.connect("user-1", ws)
        count = await manager.send_to_user("user-1", {"type": "test"})
        assert count == 0
        assert len(manager._connections.get("user-1", [])) == 0

    def test_disconnect_removes_connection(self):
        manager = ConnectionManager()
        manager._connections["user-1"] = [MagicMock()]
        ws = manager._connections["user-1"][0]
        manager.disconnect("user-1", ws)
        assert ws not in manager._connections["user-1"]

    @pytest.mark.asyncio
    async def test_multiple_connections_same_user(self):
        manager = ConnectionManager()
        ws1, ws2 = AsyncMock(), AsyncMock()
        await manager.connect("user-1", ws1)
        await manager.connect("user-1", ws2)
        assert len(manager._connections["user-1"]) == 2
        count = await manager.send_to_user("user-1", {"msg": "broadcast"})
        assert count == 2


class TestFCMHandler:
    def test_stub_mode_when_no_credentials(self):
        handler = FCMHandler()
        assert not handler._initialized  # No credentials in test env

    @pytest.mark.asyncio
    async def test_stub_send_returns_false(self):
        handler = FCMHandler()
        result = await handler.send("fake-token", "Title", "Body")
        assert result is False


class TestWebPushHandler:
    def test_stub_mode_when_no_vapid(self):
        handler = WebPushHandler()
        # In test env, VAPID keys are not set
        assert not handler._available

    @pytest.mark.asyncio
    async def test_stub_send_returns_false(self):
        handler = WebPushHandler()
        result = await handler.send({"endpoint": "https://example.com", "keys": {}}, "Title", "Body")
        assert result is False


class TestEmailHandler:
    @pytest.mark.asyncio
    async def test_stub_send_returns_false_without_smtp(self):
        handler = EmailHandler()
        result = await handler.send("user@example.com", "Subject", "Body")
        assert result is False


from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.main import Base, get_db, TokenPayload
from httpx import AsyncClient, ASGITransport
from app.main import app

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSession = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(scope="session", autouse=True)
async def setup_db():
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
async def client(db):
    from app.main import get_current_user
    
    mock_user = TokenPayload(
        sub="user-123",
        tenant_id="tenant-456",
        roles=["sales_rep"],
        exp=9999999999,
        iat=1000000000,
        jti="test-jti"
    )

    async def override_db():
        yield db

    async def override_auth():
        return mock_user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_auth
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_health_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["service"] == "notification"
    assert "channels" in data
    assert "websocket" in data["channels"]
    assert "fcm" in data["channels"]
    assert "webpush" in data["channels"]
    assert "email" in data["channels"]


@pytest.mark.asyncio
async def test_vapid_public_key_returns_503_without_config():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/subscriptions/vapid-public-key")
    # No VAPID key configured in test env → 503
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_register_list_and_delete_subscription(client):
    # Register subscription
    payload = {
        "endpoint": "https://fcm.googleapis.com/fcm/send/fake-endpoint-id",
        "keys": {"p256dh": "fake-p256dh", "auth": "fake-auth"},
        "user_agent": "Mozilla/5.0"
    }
    r = await client.post("/subscriptions/webpush", json=payload)
    assert r.status_code == 201
    sub_id = r.json()["id"]

    # List subscriptions
    r = await client.get("/subscriptions/webpush")
    assert r.status_code == 200
    assert len(r.json()) > 0

    # Delete subscription
    r = await client.delete(f"/subscriptions/webpush/{sub_id}")
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_send_notification_all_channels(client):
    # 1. Register subscription for user-123
    payload = {
        "endpoint": "https://fcm.googleapis.com/fcm/send/fake-endpoint-id",
        "keys": {"p256dh": "fake-p256dh", "auth": "fake-auth"},
        "user_agent": "Mozilla/5.0"
    }
    await client.post("/subscriptions/webpush", json=payload)

    # 2. Send notification hitting all channels (websocket, fcm, webpush, email)
    payload_send = {
        "event_type": "OrderCreated",
        "title": "New Order",
        "body": "Order #123 has been created",
        "user_id": "user-123",
        "fcm_token": "fake-fcm-token",
        "email_to": "buyer@example.com"
    }
    r = await client.post("/send", json=payload_send)
    assert r.status_code == 200
    assert "dispatched" in r.json()


@pytest.mark.asyncio
async def test_delete_subscription_not_found(client):
    r = await client.delete(f"/subscriptions/webpush/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_send_notification_no_subscription(client):
    payload = {
        "event_type": "OrderCreated",
        "title": "New Order",
        "body": "No subscription test",
        "user_id": "nonexistent-user-id"
    }
    r = await client.post("/send", json=payload)
    assert r.status_code == 200
    assert "dispatched" in r.json()


@pytest.mark.asyncio
async def test_register_subscription_conflict(client):
    payload = {
        "endpoint": "https://fcm.googleapis.com/fcm/send/conflict-endpoint",
        "keys": {"p256dh": "fake-p256dh", "auth": "fake-auth"}
    }
    # Register once
    r = await client.post("/subscriptions/webpush", json=payload)
    assert r.status_code == 201
    
    # Register twice (conflict)
    r = await client.post("/subscriptions/webpush", json=payload)
    assert r.status_code == 409


def test_websocket_endpoint_sync():
    from fastapi.testclient import TestClient
    client = TestClient(app)
    with patch("app.main.jwt.decode") as mock_decode:
        mock_decode.return_value = {"sub": "user-123"}
        with client.websocket_connect("/ws/user-123?token=mocked") as websocket:
            data = websocket.receive_json()
            assert data["type"] == "connected"
            assert data["user_id"] == "user-123"
