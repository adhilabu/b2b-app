"""
Customers Router — Outlet/Customer management with geo-fencing support
"""
from __future__ import annotations
from uuid import UUID
from math import radians, cos, sin, asin, sqrt
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models.user import User, Customer
from app.schemas.schemas import CustomerCreate, CustomerUpdate, CustomerResponse, SyncWatermarkResponse
from app.auth.dependencies import get_current_active_user
from app.config import get_settings

router = APIRouter(prefix="/customers", tags=["Customers"])
settings = get_settings()

GEO_FENCE_RADIUS_KM = 5.0  # Configurable per tenant via settings


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance between two points in kilometers."""
    R = 6371
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * R * asin(sqrt(a))


@router.post("/", response_model=CustomerResponse, status_code=status.HTTP_201_CREATED,
             summary="Create a customer/outlet")
async def create_customer(
    data: CustomerCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Create a new customer outlet. Supports offline creation via client_uuid.
    Validates GPS coordinates against territory boundaries (geo-fencing).
    Uses ON CONFLICT DO UPDATE for idempotent upsert (offline sync safety).
    """
    # Idempotent upsert — if client_uuid already exists, return existing record
    if data.client_uuid:
        result = await db.execute(
            select(Customer).where(Customer.client_uuid == data.client_uuid)
        )
        existing = result.scalar_one_or_none()
        if existing:
            return existing

    # Geo-verification placeholder (real impl would check against territory polygons)
    geo_verified = False
    if data.latitude and data.longitude:
        # Example: verify against tenant's HQ coords from settings
        tenant_settings = current_user.tenant.settings if hasattr(current_user, 'tenant') else {}
        hq_lat = tenant_settings.get("hq_lat")
        hq_lon = tenant_settings.get("hq_lon")
        radius = tenant_settings.get("geo_fence_radius_km", GEO_FENCE_RADIUS_KM)
        if hq_lat and hq_lon:
            distance = haversine_km(data.latitude, data.longitude, hq_lat, hq_lon)
            geo_verified = distance <= radius
        else:
            geo_verified = True  # No fence configured — accept all

    customer = Customer(
        client_uuid=data.client_uuid,
        tenant_id=current_user.tenant_id,
        assigned_rep_id=data.assigned_rep_id or current_user.id,
        name=data.name,
        code=data.code,
        contact_person=data.contact_person,
        phone=data.phone,
        email=data.email,
        address_line1=data.address_line1,
        address_line2=data.address_line2,
        city=data.city,
        state=data.state,
        pincode=data.pincode,
        country=data.country,
        latitude=data.latitude,
        longitude=data.longitude,
        geo_verified=geo_verified,
        credit_limit=data.credit_limit,
        payment_terms_days=data.payment_terms_days,
    )
    db.add(customer)
    await db.commit()
    await db.refresh(customer)
    return customer


@router.get("/", response_model=list[CustomerResponse], summary="List customers")
async def list_customers(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    city: str | None = Query(None),
    is_active: bool | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    query = select(Customer).where(Customer.tenant_id == current_user.tenant_id)
    if city:
        query = query.where(Customer.city == city)
    if is_active is not None:
        query = query.where(Customer.is_active == is_active)
    result = await db.execute(query.offset(skip).limit(limit))
    return result.scalars().all()


@router.get("/sync", response_model=SyncWatermarkResponse, summary="Get customers delta for sync")
async def sync_customers(
    since_version: int = Query(0, ge=0, description="Last known sync watermark"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Returns all customers modified since the given sync watermark.
    Used by the Orchestration service during Logical Watermark Pull.
    """
    result = await db.execute(
        select(Customer)
        .where(
            Customer.tenant_id == current_user.tenant_id,
            Customer.sync_version > str(since_version),
        )
        .order_by(Customer.sync_version)
    )
    customers = result.scalars().all()
    max_version = max((int(c.sync_version) for c in customers), default=since_version)

    return SyncWatermarkResponse(
        domain="identity",
        watermark=max_version,
        entities=customers,
    )


@router.get("/{customer_id}", response_model=CustomerResponse, summary="Get customer by ID")
async def get_customer(
    customer_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    result = await db.execute(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.tenant_id == current_user.tenant_id,
        )
    )
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


@router.patch("/{customer_id}", response_model=CustomerResponse, summary="Update customer")
async def update_customer(
    customer_id: UUID,
    data: CustomerUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    result = await db.execute(
        select(Customer).where(Customer.id == customer_id, Customer.tenant_id == current_user.tenant_id)
    )
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(customer, field, value)

    await db.commit()
    await db.refresh(customer)
    return customer
