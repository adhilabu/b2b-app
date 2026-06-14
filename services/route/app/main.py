"""
Route Service — FastAPI Application
Beat Plan management and VRP-optimized Route planning.
"""
import uuid
import logging
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Optional
from uuid import UUID

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pydantic_settings import BaseSettings
from functools import lru_cache
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Integer,
    Float, Date, BigInteger, func, Index
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy import select

from app.optimizer.vrp_solver import Stop, optimize_route
from jose import jwt, JWTError
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logging.basicConfig(level="INFO")
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://b2b_admin:change_me@localhost:5432/route_db"
    REDIS_URL: str = "redis://localhost:6379/3"
    PULSAR_SERVICE_URL: Optional[str] = None
    JWT_PUBLIC_KEY_PATH: str = "../../infra/keys/public.pem"
    JWT_ALGORITHM: str = "RS256"
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]
    DEBUG: bool = False

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

class Beat(Base):
    __tablename__ = "beats"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PGUUID(as_uuid=True), nullable=False, index=True)
    user_id = Column(PGUUID(as_uuid=True), nullable=False)
    name = Column(String(255), nullable=False)
    scheduled_date = Column(Date, nullable=False)
    is_optimized = Column(Boolean, default=False)
    sync_version = Column(BigInteger, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_beats_tenant_date", "tenant_id", "scheduled_date"),
    )


class BeatStop(Base):
    __tablename__ = "beat_stops"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    beat_id = Column(PGUUID(as_uuid=True), ForeignKey("beats.id", ondelete="CASCADE"), nullable=False, index=True)
    customer_id = Column(PGUUID(as_uuid=True), nullable=False)
    customer_name = Column(String(255), nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    sequence = Column(Integer, nullable=False, default=0)
    estimated_visit_minutes = Column(Integer, default=15)
    visit_notes = Column(String(500), nullable=True)


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

class BeatStopIn(BaseModel):
    customer_id: UUID
    customer_name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    estimated_visit_minutes: int = 15
    visit_notes: Optional[str] = None


class BeatCreate(BaseModel):
    name: str
    scheduled_date: date
    stops: list[BeatStopIn]


class BeatStopResponse(BaseModel):
    id: UUID
    customer_id: UUID
    customer_name: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    sequence: int
    model_config = {"from_attributes": True}


class BeatResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    user_id: UUID
    name: str
    scheduled_date: date
    is_optimized: bool
    sync_version: int
    created_at: datetime
    model_config = {"from_attributes": True}


class BeatDetailResponse(BeatResponse):
    stops: list[BeatStopResponse] = []


class OptimizeResponse(BaseModel):
    beat_id: UUID
    is_optimized: bool
    optimized_sequence: list[str]  # ordered customer_ids


# ─────────────────────────────────────────────
# LIFESPAN
# ─────────────────────────────────────────────

from app.consumers.event_consumer import RouteEventConsumer

_route_consumer: RouteEventConsumer = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _route_consumer
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    _route_consumer = RouteEventConsumer(settings.PULSAR_SERVICE_URL)
    await _route_consumer.start()

    logger.info("✅ Route Service ready")
    yield

    if _route_consumer:
        await _route_consumer.stop()
    await engine.dispose()


app = FastAPI(
    title="Route Service",
    description="Beat plans, route plans, and VRP optimization via Google OR-Tools.",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=settings.CORS_ORIGINS,
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


# ─────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────

@app.post("/beats/", response_model=BeatResponse, status_code=201, tags=["Beats"])
async def create_beat(
    data: BeatCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    beat = Beat(
        tenant_id=current_user.tenant_id,
        user_id=current_user.sub,
        name=data.name,
        scheduled_date=data.scheduled_date,
    )
    db.add(beat)
    await db.flush()

    for idx, stop_data in enumerate(data.stops):
        stop = BeatStop(
            beat_id=beat.id,
            customer_id=stop_data.customer_id,
            customer_name=stop_data.customer_name,
            latitude=stop_data.latitude,
            longitude=stop_data.longitude,
            sequence=idx,
            estimated_visit_minutes=stop_data.estimated_visit_minutes,
            visit_notes=stop_data.visit_notes,
        )
        db.add(stop)

    await db.commit()
    await db.refresh(beat)
    return beat


@app.get("/beats/", response_model=list[BeatResponse], tags=["Beats"])
async def list_beats(
    scheduled_date: Optional[date] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    query = select(Beat).where(Beat.tenant_id == current_user.tenant_id)
    if scheduled_date:
        query = query.where(Beat.scheduled_date == scheduled_date)
    result = await db.execute(query.offset(skip).limit(limit).order_by(Beat.scheduled_date.desc()))
    return result.scalars().all()


@app.get("/beats/{beat_id}", response_model=BeatDetailResponse, tags=["Beats"])
async def get_beat(
    beat_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    result = await db.execute(
        select(Beat).where(Beat.id == beat_id, Beat.tenant_id == current_user.tenant_id)
    )
    beat = result.scalar_one_or_none()
    if not beat:
        raise HTTPException(status_code=404, detail="Beat not found")

    stops_result = await db.execute(
        select(BeatStop).where(BeatStop.beat_id == beat_id).order_by(BeatStop.sequence)
    )
    stops = stops_result.scalars().all()

    return BeatDetailResponse(
        **{c.key: getattr(beat, c.key) for c in Beat.__table__.columns},
        stops=stops,
    )


@app.post("/beats/{beat_id}/optimize", response_model=OptimizeResponse, tags=["Beats"])
async def optimize_beat(
    beat_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    """
    Run OR-Tools VRP optimizer on the beat's stops.
    Reorders beat_stops by optimal sequence and marks beat as optimized.
    """
    result = await db.execute(
        select(Beat).where(Beat.id == beat_id, Beat.tenant_id == current_user.tenant_id)
    )
    beat = result.scalar_one_or_none()
    if not beat:
        raise HTTPException(status_code=404, detail="Beat not found")

    stops_result = await db.execute(
        select(BeatStop).where(BeatStop.beat_id == beat_id)
    )
    stops = stops_result.scalars().all()

    # Only optimize stops with geo-coordinates
    geo_stops = [s for s in stops if s.latitude and s.longitude]
    non_geo_stops = [s for s in stops if not (s.latitude and s.longitude)]

    if len(geo_stops) <= 1:
        return OptimizeResponse(
            beat_id=beat_id,
            is_optimized=False,
            optimized_sequence=[str(s.customer_id) for s in stops],
        )

    vrp_stops = [Stop(id=str(s.customer_id), lat=s.latitude, lon=s.longitude) for s in geo_stops]
    optimized_indices = optimize_route(vrp_stops)

    # Update sequence in DB
    for new_seq, orig_idx in enumerate(optimized_indices):
        geo_stops[orig_idx].sequence = new_seq

    # Non-geo stops go at the end
    for idx, s in enumerate(non_geo_stops):
        s.sequence = len(optimized_indices) + idx

    beat.is_optimized = True
    beat.sync_version += 1
    await db.commit()

    ordered_ids = [str(geo_stops[i].customer_id) for i in optimized_indices]
    ordered_ids += [str(s.customer_id) for s in non_geo_stops]

    return OptimizeResponse(
        beat_id=beat_id,
        is_optimized=True,
        optimized_sequence=ordered_ids,
    )


@app.get("/sync/", tags=["Sync"])
async def sync_beats(
    since_version: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    result = await db.execute(
        select(Beat).where(
            Beat.tenant_id == current_user.tenant_id,
            Beat.sync_version > since_version,
        )
    )
    beats = result.scalars().all()
    max_v = max((b.sync_version for b in beats), default=since_version)
    return {"domain": "route", "watermark": max_v, "beats": [BeatResponse.model_validate(b) for b in beats]}


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "healthy", "service": "route"}


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
async def receive_sync_event(
    event: SyncEventIn,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    """
    Receives offline beat plan events from the Orchestration service.
    Handles BeatPlanCreated (upsert beat + stops) and RouteOptimized (mark optimized).
    """
    if event.event_type == "BeatPlanCreated":
        payload = event.payload
        client_beat_id = payload.get("beat_id")

        scheduled_date_str = payload.get("scheduled_date")
        if not scheduled_date_str:
            return {"event_id": event.event_id, "status": "skipped", "reason": "missing scheduled_date"}

        from datetime import date as date_type
        try:
            scheduled_date = date_type.fromisoformat(scheduled_date_str)
        except ValueError:
            return {"event_id": event.event_id, "status": "rejected", "reason": "invalid scheduled_date format"}

        # Idempotent: check if beat with this client ID already exists
        beat = None
        if client_beat_id:
            existing = await db.execute(
                select(Beat).where(
                    Beat.tenant_id == current_user.tenant_id,
                    Beat.id == client_beat_id,
                )
            )
            beat = existing.scalar_one_or_none()

        if beat is None:
            beat = Beat(
                tenant_id=current_user.tenant_id,
                user_id=payload.get("user_id") or current_user.sub,
                name=payload.get("name", "Offline Beat"),
                scheduled_date=scheduled_date,
                is_optimized=False,
                sync_version=0,
            )
            db.add(beat)
            await db.flush()

            for idx, stop_data in enumerate(payload.get("stops", [])):
                stop = BeatStop(
                    beat_id=beat.id,
                    customer_id=stop_data["customer_id"],
                    customer_name=stop_data.get("customer_name", ""),
                    latitude=stop_data.get("latitude"),
                    longitude=stop_data.get("longitude"),
                    sequence=idx,
                    estimated_visit_minutes=stop_data.get("estimated_visit_minutes", 30),
                )
                db.add(stop)

        await db.commit()
        return {"event_id": event.event_id, "status": "accepted", "beat_id": str(beat.id)}

    elif event.event_type == "RouteOptimized":
        beat_id = event.payload.get("beat_id")
        if not beat_id:
            return {"event_id": event.event_id, "status": "skipped", "reason": "missing beat_id"}

        result = await db.execute(select(Beat).where(Beat.id == beat_id))
        beat = result.scalar_one_or_none()
        if beat:
            beat.is_optimized = True
            beat.sync_version += 1
            await db.commit()
        return {"event_id": event.event_id, "status": "accepted"}

    return {"event_id": event.event_id, "status": "skipped", "reason": f"unhandled event type: {event.event_type}"}
