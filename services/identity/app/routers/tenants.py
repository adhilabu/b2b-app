"""
Tenants Router — Tenant and Settings Management
"""
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models.user import User, Tenant
from app.schemas.schemas import TenantCreate, TenantUpdate, TenantResponse
from app.auth.rbac import require_admin

router = APIRouter(prefix="/tenants", tags=["Tenants"])


@router.post("/", response_model=TenantResponse, status_code=status.HTTP_201_CREATED,
             summary="Create a new tenant (system admin only)")
async def create_tenant(
    data: TenantCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    existing = await db.execute(select(Tenant).where(Tenant.slug == data.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Tenant slug already exists")

    tenant = Tenant(name=data.name, slug=data.slug, settings=data.settings)
    db.add(tenant)
    await db.commit()
    await db.refresh(tenant)
    return tenant


@router.get("/me", response_model=TenantResponse, summary="Get current user's tenant")
async def get_my_tenant(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    result = await db.execute(select(Tenant).where(Tenant.id == current_user.tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


@router.patch("/{tenant_id}", response_model=TenantResponse, summary="Update tenant settings")
async def update_tenant(
    tenant_id: UUID,
    data: TenantUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Security: ensure user belongs to this tenant
    if tenant.id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Cannot modify another tenant")

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(tenant, field, value)

    await db.commit()
    await db.refresh(tenant)
    return tenant
