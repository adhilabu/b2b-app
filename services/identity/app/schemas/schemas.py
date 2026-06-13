from uuid import UUID
from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, EmailStr, field_validator
from app.models.user import UserRole


# ─────────────────────────────────────────────
# AUTH SCHEMAS
# ─────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPayload(BaseModel):
    sub: str          # user_id
    tenant_id: str
    roles: list[str]
    exp: int
    iat: int
    jti: str


# ─────────────────────────────────────────────
# USER SCHEMAS
# ─────────────────────────────────────────────

class UserBase(BaseModel):
    email: EmailStr
    full_name: str
    phone: Optional[str] = None
    role: UserRole = UserRole.sales_rep


class UserCreate(UserBase):
    password: str
    tenant_id: UUID

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None


class UserResponse(UserBase):
    id: UUID
    tenant_id: UUID
    is_active: bool
    is_verified: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────
# TENANT SCHEMAS
# ─────────────────────────────────────────────

class TenantCreate(BaseModel):
    name: str
    slug: str
    settings: dict[str, Any] = {}


class TenantUpdate(BaseModel):
    name: Optional[str] = None
    settings: Optional[dict[str, Any]] = None
    is_active: Optional[bool] = None


class TenantResponse(BaseModel):
    id: UUID
    name: str
    slug: str
    is_active: bool
    settings: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────
# CUSTOMER SCHEMAS
# ─────────────────────────────────────────────

class CustomerBase(BaseModel):
    name: str
    code: Optional[str] = None
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None
    country: str = "India"
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    credit_limit: float = 0.0
    payment_terms_days: Optional[str] = None


class CustomerCreate(CustomerBase):
    client_uuid: Optional[UUID] = None
    assigned_rep_id: Optional[UUID] = None


class CustomerUpdate(BaseModel):
    name: Optional[str] = None
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    credit_limit: Optional[float] = None
    is_active: Optional[bool] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class CustomerResponse(CustomerBase):
    id: UUID
    client_uuid: Optional[UUID]
    tenant_id: UUID
    assigned_rep_id: Optional[UUID]
    is_active: bool
    geo_verified: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────
# SYNC SCHEMAS
# ─────────────────────────────────────────────

class SyncWatermarkResponse(BaseModel):
    domain: str = "identity"
    watermark: int
    entities: list[CustomerResponse] = []
