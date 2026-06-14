"""
Notification Service — FastAPI Application
4 Delivery Channels:
  1. WebSocket — In-app real-time (active sessions)
  2. FCM (Firebase) — Mobile push (Android/iOS)
  3. Web Push (VAPID) — Browser push notifications
  4. Email — Async fallback via SMTP

Listens to Apache Pulsar domain events and dispatches notifications.
"""
import uuid
import json
import logging
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from pydantic_settings import BaseSettings
from functools import lru_cache
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Column, String, DateTime, Boolean, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB
from sqlalchemy import select
from jose import jwt, JWTError
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logging.basicConfig(level="INFO")
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://b2b_admin:change_me@localhost:5432/notification_db"
    REDIS_URL: str = "redis://localhost:6379/5"
    PULSAR_SERVICE_URL: Optional[str] = None
    JWT_PUBLIC_KEY_PATH: str = "../../infra/keys/public.pem"
    JWT_ALGORITHM: str = "RS256"
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]
    DEBUG: bool = False
    FIREBASE_CREDENTIALS_PATH: Optional[str] = None
    VAPID_PRIVATE_KEY: Optional[str] = None
    VAPID_PUBLIC_KEY: Optional[str] = None
    VAPID_CLAIMS_EMAIL: str = "admin@yourdomain.com"
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    EMAIL_FROM: str = "noreply@yourdomain.com"

    @property
    def jwt_public_key(self) -> str:
        with open(self.JWT_PUBLIC_KEY_PATH) as f:
            return f.read()

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
engine = create_async_engine(settings.DATABASE_URL, echo=settings.DEBUG, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as s:
        try:
            yield s
        except Exception:
            await s.rollback()
            raise


# ─────────────────────────────────────────────
# MODELS
# ─────────────────────────────────────────────

class WebPushSubscription(Base):
    """Stores browser Web Push subscription objects per user."""
    __tablename__ = "web_push_subscriptions"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(String(255), nullable=False, index=True)
    tenant_id = Column(String(255), nullable=False, index=True)
    endpoint = Column(Text, nullable=False, unique=True)
    keys = Column(JSONB, nullable=False)  # {"p256dh": "...", "auth": "..."}
    user_agent = Column(String(500), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class NotificationLog(Base):
    """Audit log of all sent notifications."""
    __tablename__ = "notification_logs"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(String(255), nullable=True)
    tenant_id = Column(String(255), nullable=True)
    channel = Column(String(50), nullable=False)  # websocket|fcm|webpush|email
    event_type = Column(String(100), nullable=False)
    title = Column(String(255), nullable=True)
    body = Column(Text, nullable=True)
    is_sent = Column(Boolean, default=False)
    error = Column(Text, nullable=True)
    sent_at = Column(DateTime(timezone=True), server_default=func.now())


# ─────────────────────────────────────────────
# WEBSOCKET CONNECTION MANAGER
# ─────────────────────────────────────────────

class ConnectionManager:
    """Manages active WebSocket connections, grouped by user_id."""

    def __init__(self):
        self._connections: dict[str, list[WebSocket]] = {}

    async def connect(self, user_id: str, ws: WebSocket):
        await ws.accept()
        self._connections.setdefault(user_id, []).append(ws)
        logger.info(f"WS connected: user={user_id} total={sum(len(v) for v in self._connections.values())}")

    def disconnect(self, user_id: str, ws: WebSocket):
        if user_id in self._connections:
            self._connections[user_id].discard(ws) if hasattr(self._connections[user_id], 'discard') \
                else self._connections[user_id].remove(ws) if ws in self._connections[user_id] else None

    async def send_to_user(self, user_id: str, message: dict) -> int:
        """Send message to all connections for a user. Returns count sent."""
        sent = 0
        dead_connections = []
        for ws in self._connections.get(user_id, []):
            try:
                await ws.send_json(message)
                sent += 1
            except Exception:
                dead_connections.append(ws)
        for ws in dead_connections:
            self._connections[user_id].remove(ws)
        return sent

    async def broadcast_to_tenant(self, tenant_id: str, message: dict, tenant_users: list[str]):
        """Broadcast to all connected users in a tenant."""
        for uid in tenant_users:
            await self.send_to_user(uid, message)

    async def send_notification(self, user_id: str, payload: dict) -> None:
        """Send a notification payload to a specific user's active WebSocket connections."""
        await self.send_to_user(user_id, payload)


ws_manager = ConnectionManager()


# ─────────────────────────────────────────────
# FCM HANDLER
# ─────────────────────────────────────────────

class FCMHandler:
    def __init__(self):
        self._initialized = False
        if settings.FIREBASE_CREDENTIALS_PATH:
            try:
                import firebase_admin
                from firebase_admin import credentials
                cred = credentials.Certificate(settings.FIREBASE_CREDENTIALS_PATH)
                firebase_admin.initialize_app(cred)
                self._initialized = True
                logger.info("✅ Firebase FCM initialized")
            except Exception as e:
                logger.warning(f"⚠️ FCM init failed: {e}")

    async def send(self, token: str, title: str, body: str, data: dict = None) -> bool:
        if not self._initialized:
            logger.info(f"[STUB FCM] Title: {title}, Body: {body}")
            return False
        try:
            from firebase_admin import messaging
            msg = messaging.Message(
                notification=messaging.Notification(title=title, body=body),
                data={k: str(v) for k, v in (data or {}).items()},
                token=token,
            )
            messaging.send(msg)
            return True
        except Exception as e:
            logger.error(f"FCM send error: {e}")
            return False


# ─────────────────────────────────────────────
# WEB PUSH (VAPID) HANDLER
# ─────────────────────────────────────────────

class WebPushHandler:
    def __init__(self):
        self._available = bool(settings.VAPID_PRIVATE_KEY and settings.VAPID_PUBLIC_KEY)

    async def send(self, subscription_info: dict, title: str, body: str, data: dict = None) -> bool:
        if not self._available:
            logger.info(f"[STUB VAPID] Title: {title}, Body: {body}")
            return False
        try:
            from pywebpush import webpush, WebPushException
            payload = json.dumps({"title": title, "body": body, **(data or {})})
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims={"sub": f"mailto:{settings.VAPID_CLAIMS_EMAIL}"},
            )
            return True
        except Exception as e:
            logger.error(f"WebPush error: {e}")
            return False


# ─────────────────────────────────────────────
# EMAIL HANDLER
# ─────────────────────────────────────────────

class EmailHandler:
    async def send(self, to_email: str, subject: str, body: str) -> bool:
        if not settings.SMTP_USER:
            logger.info(f"[STUB EMAIL] To: {to_email}, Subject: {subject}")
            return False
        try:
            import aiosmtplib
            from email.message import EmailMessage
            msg = EmailMessage()
            msg["From"] = settings.EMAIL_FROM
            msg["To"] = to_email
            msg["Subject"] = subject
            msg.set_content(body)
            await aiosmtplib.send(
                msg,
                hostname=settings.SMTP_HOST,
                port=settings.SMTP_PORT,
                username=settings.SMTP_USER,
                password=settings.SMTP_PASSWORD,
                start_tls=True,
            )
            return True
        except Exception as e:
            logger.error(f"Email send error: {e}")
            return False


# ─────────────────────────────────────────────
# NOTIFICATION DISPATCHER
# ─────────────────────────────────────────────

class NotificationDispatcher:
    def __init__(self):
        self.fcm = FCMHandler()
        self.webpush = WebPushHandler()
        self.email = EmailHandler()

    async def dispatch(
        self,
        event_type: str,
        title: str,
        body: str,
        user_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        fcm_token: Optional[str] = None,
        email_to: Optional[str] = None,
        web_push_subscription: Optional[dict] = None,
        extra_data: dict = None,
    ):
        results = {}

        # 1. WebSocket
        if user_id:
            ws_count = await ws_manager.send_to_user(user_id, {
                "type": event_type,
                "title": title,
                "body": body,
                "data": extra_data or {},
                "timestamp": datetime.utcnow().isoformat(),
            })
            results["websocket"] = ws_count

        # 2. FCM
        if fcm_token:
            results["fcm"] = await self.fcm.send(fcm_token, title, body, extra_data)

        # 3. Web Push
        if web_push_subscription:
            results["webpush"] = await self.webpush.send(web_push_subscription, title, body, extra_data)

        # 4. Email
        if email_to:
            results["email"] = await self.email.send(email_to, title, body)

        logger.info(f"Notification dispatched [{event_type}]: {results}")
        return results


dispatcher = NotificationDispatcher()


# ─────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────

bearer = HTTPBearer()


class TokenPayload(BaseModel):
    sub: str
    tenant_id: str
    roles: list[str]
    exp: int
    iat: int
    jti: str


async def get_current_user(creds: HTTPAuthorizationCredentials = Depends(bearer)) -> TokenPayload:
    try:
        payload = jwt.decode(creds.credentials, settings.jwt_public_key, algorithms=[settings.JWT_ALGORITHM])
        return TokenPayload(**payload)
    except JWTError as e:
        raise HTTPException(status_code=401, detail=str(e))


# ─────────────────────────────────────────────
# SCHEMAS
# ─────────────────────────────────────────────

class WebPushSubscriptionCreate(BaseModel):
    endpoint: str
    keys: dict  # {"p256dh": "...", "auth": "..."}
    user_agent: Optional[str] = None


class WebPushSubscriptionResponse(BaseModel):
    id: UUID
    user_id: str
    endpoint: str
    is_active: bool
    created_at: datetime
    model_config = {"from_attributes": True}


class SendNotificationRequest(BaseModel):
    event_type: str
    title: str
    body: str
    user_id: Optional[str] = None
    fcm_token: Optional[str] = None
    email_to: Optional[EmailStr] = None
    extra_data: dict = {}


# ─────────────────────────────────────────────
# LIFESPAN
# ─────────────────────────────────────────────

from app.consumers.event_consumer import PulsarEventConsumer

_pulsar_consumer: PulsarEventConsumer = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pulsar_consumer
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    _pulsar_consumer = PulsarEventConsumer(settings.PULSAR_SERVICE_URL, ws_manager)
    await _pulsar_consumer.start()

    logger.info("✅ Notification Service ready")
    yield

    if _pulsar_consumer:
        await _pulsar_consumer.stop()
    await engine.dispose()


app = FastAPI(
    title="Notification Service",
    description="Multi-channel notifications: WebSocket (real-time), FCM (mobile), Web Push (VAPID/browser), Email.",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=settings.CORS_ORIGINS,
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


# ─────────────────────────────────────────────
# WEBSOCKET ENDPOINT
# ─────────────────────────────────────────────

@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str, token: str = Query(...)):
    """
    WebSocket connection endpoint.
    Client connects with: ws://host/ws/{user_id}?token=<JWT>

    Validates JWT before accepting the connection.
    """
    try:
        jwt.decode(token, settings.jwt_public_key, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        await websocket.close(code=4001, reason="Invalid token")
        return

    await ws_manager.connect(user_id, websocket)
    try:
        await websocket.send_json({"type": "connected", "user_id": user_id})
        while True:
            # Keep connection alive — receive heartbeat pings
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(user_id, websocket)
        logger.info(f"WS disconnected: user={user_id}")


# ─────────────────────────────────────────────
# WEB PUSH SUBSCRIPTION ENDPOINTS
# ─────────────────────────────────────────────

@app.post("/subscriptions/webpush", response_model=WebPushSubscriptionResponse,
          status_code=201, tags=["Web Push"])
async def register_webpush_subscription(
    data: WebPushSubscriptionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    """Register a browser Web Push subscription (VAPID)."""
    # Check if endpoint already registered
    existing = await db.execute(
        select(WebPushSubscription).where(WebPushSubscription.endpoint == data.endpoint)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Subscription endpoint already registered")

    sub = WebPushSubscription(
        user_id=current_user.sub,
        tenant_id=current_user.tenant_id,
        endpoint=data.endpoint,
        keys=data.keys,
        user_agent=data.user_agent,
    )
    db.add(sub)
    await db.commit()
    await db.refresh(sub)
    return sub


@app.get("/subscriptions/webpush", response_model=list[WebPushSubscriptionResponse], tags=["Web Push"])
async def list_webpush_subscriptions(
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    result = await db.execute(
        select(WebPushSubscription).where(
            WebPushSubscription.user_id == current_user.sub,
            WebPushSubscription.is_active == True,
        )
    )
    return result.scalars().all()


@app.get("/subscriptions/vapid-public-key", tags=["Web Push"])
async def get_vapid_public_key():
    """Returns the VAPID public key for the browser to use when subscribing."""
    if not settings.VAPID_PUBLIC_KEY:
        raise HTTPException(status_code=503, detail="VAPID not configured")
    return {"vapid_public_key": settings.VAPID_PUBLIC_KEY}


@app.delete("/subscriptions/webpush/{subscription_id}", status_code=204, tags=["Web Push"])
async def delete_webpush_subscription(
    subscription_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    result = await db.execute(
        select(WebPushSubscription).where(
            WebPushSubscription.id == subscription_id,
            WebPushSubscription.user_id == current_user.sub,
        )
    )
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    sub.is_active = False
    await db.commit()


# ─────────────────────────────────────────────
# INTERNAL SEND ENDPOINT (used by Orchestration)
# ─────────────────────────────────────────────

@app.post("/send", tags=["Send"])
async def send_notification(
    data: SendNotificationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    """Internal endpoint for sending notifications. Called by Orchestration service."""
    # Fetch web push subscriptions for the user
    web_push_sub = None
    if data.user_id:
        result = await db.execute(
            select(WebPushSubscription).where(
                WebPushSubscription.user_id == data.user_id,
                WebPushSubscription.is_active == True,
            ).limit(1)
        )
        sub = result.scalar_one_or_none()
        if sub:
            web_push_sub = {"endpoint": sub.endpoint, "keys": sub.keys}

    results = await dispatcher.dispatch(
        event_type=data.event_type,
        title=data.title,
        body=data.body,
        user_id=data.user_id,
        fcm_token=data.fcm_token,
        email_to=data.email_to,
        web_push_subscription=web_push_sub,
        extra_data=data.extra_data,
    )
    return {"dispatched": results}


@app.get("/health", tags=["Health"])
async def health():
    return {
        "status": "healthy",
        "service": "notification",
        "channels": {
            "websocket": "active",
            "fcm": "configured" if settings.FIREBASE_CREDENTIALS_PATH else "stub",
            "webpush": "configured" if settings.VAPID_PRIVATE_KEY else "stub",
            "email": "configured" if settings.SMTP_USER else "stub",
        }
    }


# ─────────────────────────────────────────────
# SYNC EVENTS (Offline push from Orchestration)
# ─────────────────────────────────────────────

class SyncEventIn(BaseModel):
    event_id: str
    event_type: str
    domain: str
    timestamp: Optional[int] = None
    payload: dict = {}


@app.post("/sync/events", tags=["Sync"])
async def receive_sync_event(event: SyncEventIn):
    """
    Notification service is server-push only. Offline sync events from clients
    are acknowledged without processing — notifications flow from server to client,
    not the reverse.
    """
    logger.info(f"sync_event received: type={event.event_type} id={event.event_id}")
    return {"event_id": event.event_id, "status": "acknowledged"}
