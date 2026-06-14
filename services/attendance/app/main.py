"""
Attendance Service — FastAPI Application
Field rep check-ins, check-outs, leave management, and shift adherence.
Also provides the operational blocking API used by the Orchestration service.
"""
import uuid
import logging
from contextlib import asynccontextmanager
from datetime import date, datetime, time
from typing import Optional
from uuid import UUID
import enum

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pydantic_settings import BaseSettings
from functools import lru_cache
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import (
    Column, String, Boolean, DateTime, Enum, ForeignKey,
    Date, Time, Text, func, Index
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy import select
from jose import jwt, JWTError
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logging.basicConfig(level="INFO")
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://b2b_admin:change_me@localhost:5432/attendance_db"
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

class AttendanceStatus(str, enum.Enum):
    present = "present"
    absent = "absent"
    on_leave = "on_leave"
    half_day = "half_day"
    work_from_home = "work_from_home"


class LeaveStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    cancelled = "cancelled"


class LeaveType(str, enum.Enum):
    casual = "casual"
    sick = "sick"
    earned = "earned"
    unpaid = "unpaid"


class Attendance(Base):
    __tablename__ = "attendance"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PGUUID(as_uuid=True), nullable=False, index=True)
    user_id = Column(PGUUID(as_uuid=True), nullable=False)
    attendance_date = Column(Date, nullable=False)
    status = Column(Enum(AttendanceStatus), default=AttendanceStatus.present, nullable=False)
    check_in_at = Column(DateTime(timezone=True), nullable=True)
    check_out_at = Column(DateTime(timezone=True), nullable=True)
    check_in_lat = Column(String(20), nullable=True)
    check_in_lon = Column(String(20), nullable=True)
    check_out_lat = Column(String(20), nullable=True)
    check_out_lon = Column(String(20), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_attendance_user_date", "user_id", "attendance_date", unique=True),
    )


class LeaveRequest(Base):
    __tablename__ = "leave_requests"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PGUUID(as_uuid=True), nullable=False, index=True)
    user_id = Column(PGUUID(as_uuid=True), nullable=False, index=True)
    leave_type = Column(Enum(LeaveType), nullable=False)
    from_date = Column(Date, nullable=False)
    to_date = Column(Date, nullable=False)
    reason = Column(Text, nullable=True)
    status = Column(Enum(LeaveStatus), default=LeaveStatus.pending, nullable=False)
    approved_by = Column(PGUUID(as_uuid=True), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


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

class CheckInRequest(BaseModel):
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    notes: Optional[str] = None


class CheckOutRequest(BaseModel):
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    notes: Optional[str] = None


class AttendanceResponse(BaseModel):
    id: UUID
    user_id: UUID
    attendance_date: date
    status: AttendanceStatus
    check_in_at: Optional[datetime]
    check_out_at: Optional[datetime]
    notes: Optional[str]
    model_config = {"from_attributes": True}


class LeaveRequestCreate(BaseModel):
    leave_type: LeaveType
    from_date: date
    to_date: date
    reason: Optional[str] = None


class LeaveRequestResponse(BaseModel):
    id: UUID
    user_id: UUID
    leave_type: LeaveType
    from_date: date
    to_date: date
    status: LeaveStatus
    reason: Optional[str]
    created_at: datetime
    model_config = {"from_attributes": True}


class UserAvailabilityResponse(BaseModel):
    user_id: str
    check_date: date
    is_available: bool
    status: Optional[AttendanceStatus]
    reason: str


# ─────────────────────────────────────────────
# LIFESPAN
# ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("✅ Attendance Service ready")
    yield
    await engine.dispose()


app = FastAPI(
    title="Attendance Service",
    description="Field rep check-ins, check-outs, leave management, and operational availability blocking.",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=settings.CORS_ORIGINS,
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


# ─────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────

@app.post("/attendance/check-in", response_model=AttendanceResponse, tags=["Attendance"])
async def check_in(
    data: CheckInRequest,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    """Record a field rep check-in for today."""
    today = date.today()

    # Upsert: if already checked in today, update
    result = await db.execute(
        select(Attendance).where(
            Attendance.user_id == current_user.sub,
            Attendance.attendance_date == today,
        )
    )
    attendance = result.scalar_one_or_none()

    if attendance:
        attendance.check_in_at = datetime.now()
        attendance.status = AttendanceStatus.present
    else:
        attendance = Attendance(
            tenant_id=current_user.tenant_id,
            user_id=current_user.sub,
            attendance_date=today,
            status=AttendanceStatus.present,
            check_in_at=datetime.now(),
            check_in_lat=str(data.latitude) if data.latitude else None,
            check_in_lon=str(data.longitude) if data.longitude else None,
            notes=data.notes,
        )
        db.add(attendance)

    await db.commit()
    await db.refresh(attendance)
    return attendance


@app.post("/attendance/check-out", response_model=AttendanceResponse, tags=["Attendance"])
async def check_out(
    data: CheckOutRequest,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    """Record a field rep check-out."""
    today = date.today()
    result = await db.execute(
        select(Attendance).where(
            Attendance.user_id == current_user.sub,
            Attendance.attendance_date == today,
        )
    )
    attendance = result.scalar_one_or_none()
    if not attendance or not attendance.check_in_at:
        raise HTTPException(status_code=400, detail="No check-in found for today")

    attendance.check_out_at = datetime.now()
    attendance.check_out_lat = str(data.latitude) if data.latitude else None
    attendance.check_out_lon = str(data.longitude) if data.longitude else None
    await db.commit()
    await db.refresh(attendance)
    return attendance


@app.get("/attendance/", response_model=list[AttendanceResponse], tags=["Attendance"])
async def list_attendance(
    user_id: Optional[UUID] = Query(None),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    query = select(Attendance).where(Attendance.tenant_id == current_user.tenant_id)
    if user_id:
        query = query.where(Attendance.user_id == user_id)
    if from_date:
        query = query.where(Attendance.attendance_date >= from_date)
    if to_date:
        query = query.where(Attendance.attendance_date <= to_date)
    result = await db.execute(query.order_by(Attendance.attendance_date.desc()))
    return result.scalars().all()


@app.get("/attendance/availability/{user_id}", response_model=UserAvailabilityResponse, tags=["Attendance"])
async def check_availability(
    user_id: UUID,
    check_date: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    """
    Critical operational endpoint: Used by Orchestration service to check if
    a user is available before initiating Route or End-of-Day Settlement.
    """
    target_date = check_date or date.today()

    # Check leave requests
    leave_result = await db.execute(
        select(LeaveRequest).where(
            LeaveRequest.user_id == user_id,
            LeaveRequest.tenant_id == current_user.tenant_id,
            LeaveRequest.status == LeaveStatus.approved,
            LeaveRequest.from_date <= target_date,
            LeaveRequest.to_date >= target_date,
        )
    )
    leave = leave_result.scalar_one_or_none()
    if leave:
        return UserAvailabilityResponse(
            user_id=str(user_id),
            check_date=target_date,
            is_available=False,
            status=AttendanceStatus.on_leave,
            reason=f"Approved {leave.leave_type.value} leave",
        )

    # Check attendance record
    att_result = await db.execute(
        select(Attendance).where(
            Attendance.user_id == user_id,
            Attendance.attendance_date == target_date,
        )
    )
    attendance = att_result.scalar_one_or_none()
    if attendance and attendance.status == AttendanceStatus.absent:
        return UserAvailabilityResponse(
            user_id=str(user_id),
            check_date=target_date,
            is_available=False,
            status=AttendanceStatus.absent,
            reason="Marked absent",
        )

    return UserAvailabilityResponse(
        user_id=str(user_id),
        check_date=target_date,
        is_available=True,
        status=attendance.status if attendance else None,
        reason="Available",
    )


# ─────────────────────────────────────────────
# LEAVE ENDPOINTS
# ─────────────────────────────────────────────

@app.post("/leaves/", response_model=LeaveRequestResponse, status_code=201, tags=["Leaves"])
async def create_leave_request(
    data: LeaveRequestCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    if data.from_date > data.to_date:
        raise HTTPException(status_code=400, detail="from_date must be before to_date")

    leave = LeaveRequest(
        tenant_id=current_user.tenant_id,
        user_id=current_user.sub,
        leave_type=data.leave_type,
        from_date=data.from_date,
        to_date=data.to_date,
        reason=data.reason,
    )
    db.add(leave)
    await db.commit()
    await db.refresh(leave)
    return leave


@app.patch("/leaves/{leave_id}/approve", response_model=LeaveRequestResponse, tags=["Leaves"])
async def approve_leave(
    leave_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    if "manager" not in current_user.roles and "admin" not in current_user.roles:
        raise HTTPException(status_code=403, detail="Only managers can approve leaves")

    result = await db.execute(select(LeaveRequest).where(LeaveRequest.id == leave_id))
    leave = result.scalar_one_or_none()
    if not leave:
        raise HTTPException(status_code=404, detail="Leave request not found")
    leave.status = LeaveStatus.approved
    leave.approved_by = current_user.sub
    leave.approved_at = datetime.now()
    await db.commit()
    await db.refresh(leave)
    return leave


@app.patch("/leaves/{leave_id}/reject", response_model=LeaveRequestResponse, tags=["Leaves"])
async def reject_leave(
    leave_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    if "manager" not in current_user.roles and "admin" not in current_user.roles:
        raise HTTPException(status_code=403, detail="Only managers can reject leaves")

    result = await db.execute(select(LeaveRequest).where(LeaveRequest.id == leave_id))
    leave = result.scalar_one_or_none()
    if not leave:
        raise HTTPException(status_code=404, detail="Leave request not found")
    leave.status = LeaveStatus.rejected
    await db.commit()
    await db.refresh(leave)
    return leave


@app.get("/leaves/", response_model=list[LeaveRequestResponse], tags=["Leaves"])
async def list_leaves(
    user_id: Optional[UUID] = Query(None),
    status: Optional[LeaveStatus] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    query = select(LeaveRequest).where(LeaveRequest.tenant_id == current_user.tenant_id)
    if user_id:
        query = query.where(LeaveRequest.user_id == user_id)
    if status:
        query = query.where(LeaveRequest.status == status)
    result = await db.execute(query.order_by(LeaveRequest.created_at.desc()))
    return result.scalars().all()


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "healthy", "service": "attendance"}


# ─────────────────────────────────────────────
# SYNC EVENTS (Offline push from Orchestration)
# ─────────────────────────────────────────────

class SyncEvent(BaseModel):
    event_id: str
    event_type: str
    domain: str
    timestamp: Optional[int] = None
    payload: dict = {}


@app.post("/sync/events", tags=["Sync"])
async def receive_sync_event(
    event: SyncEvent,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    """
    Receives a single offline domain event forwarded by the Orchestration service.
    Performs idempotent upserts for AttendanceLogged and LeaveRequested events.
    """
    if event.event_type == "AttendanceLogged":
        payload = event.payload
        user_id = payload.get("user_id") or current_user.sub
        attendance_date_str = payload.get("attendance_date")
        if not attendance_date_str:
            return {"event_id": event.event_id, "status": "skipped", "reason": "missing attendance_date"}

        from datetime import date as date_type
        try:
            att_date = date_type.fromisoformat(attendance_date_str)
        except ValueError:
            return {"event_id": event.event_id, "status": "rejected", "reason": "invalid date format"}

        # Idempotent upsert: check if attendance already exists for this user + date
        existing = await db.execute(
            select(Attendance).where(
                Attendance.user_id == user_id,
                Attendance.attendance_date == att_date,
            )
        )
        att = existing.scalar_one_or_none()
        if att is None:
            att = Attendance(
                tenant_id=current_user.tenant_id,
                user_id=user_id,
                attendance_date=att_date,
                status=AttendanceStatus(payload.get("status", "present")),
                check_in_at=datetime.fromisoformat(payload["check_in_at"]) if payload.get("check_in_at") else None,
                check_in_lat=str(payload["check_in_lat"]) if payload.get("check_in_lat") else None,
                check_in_lon=str(payload["check_in_lon"]) if payload.get("check_in_lon") else None,
            )
            db.add(att)
        else:
            # Update with richer data if provided — Last-Write-Wins
            if payload.get("check_out_at"):
                att.check_out_at = datetime.fromisoformat(payload["check_out_at"])
                att.check_out_lat = str(payload["check_out_lat"]) if payload.get("check_out_lat") else None
                att.check_out_lon = str(payload["check_out_lon"]) if payload.get("check_out_lon") else None
        await db.commit()
        return {"event_id": event.event_id, "status": "accepted"}

    elif event.event_type == "LeaveRequested":
        payload = event.payload
        user_id = payload.get("user_id") or current_user.sub
        from datetime import date as date_type
        try:
            from_date = date_type.fromisoformat(payload["from_date"])
            to_date = date_type.fromisoformat(payload["to_date"])
        except (KeyError, ValueError):
            return {"event_id": event.event_id, "status": "rejected", "reason": "missing or invalid from_date/to_date"}

        leave = LeaveRequest(
            tenant_id=current_user.tenant_id,
            user_id=user_id,
            leave_type=LeaveType(payload.get("leave_type", "casual")),
            from_date=from_date,
            to_date=to_date,
            reason=payload.get("reason", ""),
            status=LeaveStatus.pending,
        )
        db.add(leave)
        await db.commit()
        return {"event_id": event.event_id, "status": "accepted"}

    return {"event_id": event.event_id, "status": "skipped", "reason": f"unhandled event type: {event.event_type}"}
