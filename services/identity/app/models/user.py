import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Boolean, DateTime, Enum, ForeignKey,
    Text, Float, func, Index
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.database import Base
import enum


class UserRole(str, enum.Enum):
    admin = "admin"
    manager = "manager"
    sales_rep = "sales_rep"
    driver = "driver"
    supervisor = "supervisor"


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    phone = Column(String(20), unique=True, nullable=True)
    full_name = Column(String(255), nullable=False)
    hashed_password = Column(String(255), nullable=False)
    role = Column(Enum(UserRole), nullable=False, default=UserRole.sales_rep)
    is_active = Column(Boolean, default=True, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    tenant = relationship("Tenant", back_populates="users")
    customers = relationship("Customer", back_populates="assigned_rep")

    __table_args__ = (
        Index("ix_users_tenant_email", "tenant_id", "email"),
    )


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    slug = Column(String(100), unique=True, nullable=False, index=True)
    is_active = Column(Boolean, default=True, nullable=False)
    settings = Column(JSONB, default={}, nullable=False)
    # e.g., {"currency": "INR", "timezone": "Asia/Kolkata", "geo_fence_radius_km": 5}
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    users = relationship("User", back_populates="tenant")
    customers = relationship("Customer", back_populates="tenant")


class Customer(Base):
    """Customer/Outlet metadata — source of truth for outlet information."""
    __tablename__ = "customers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_uuid = Column(UUID(as_uuid=True), unique=True, nullable=True, index=True)  # Offline-created UUID
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    assigned_rep_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    name = Column(String(255), nullable=False)
    code = Column(String(100), nullable=True)  # Business customer code
    contact_person = Column(String(255), nullable=True)
    phone = Column(String(20), nullable=True)
    email = Column(String(255), nullable=True)

    # Address
    address_line1 = Column(String(255), nullable=True)
    address_line2 = Column(String(255), nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)
    pincode = Column(String(20), nullable=True)
    country = Column(String(100), default="India", nullable=False)

    # Geo-fencing
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    geo_verified = Column(Boolean, default=False, nullable=False)

    is_active = Column(Boolean, default=True, nullable=False)
    credit_limit = Column(Float, default=0.0, nullable=False)
    payment_terms_days = Column(String(50), nullable=True)  # e.g., "net30"

    sync_version = Column(String, nullable=False, default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    tenant = relationship("Tenant", back_populates="customers")
    assigned_rep = relationship("User", back_populates="customers")

    __table_args__ = (
        Index("ix_customers_tenant_code", "tenant_id", "code"),
    )
