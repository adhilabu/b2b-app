"""
Users Router — CRUD for user management
"""
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from passlib.context import CryptContext

from app.database import get_db
from app.models.user import User
from app.schemas.schemas import UserCreate, UserUpdate, UserResponse
from app.auth.dependencies import get_current_active_user
from app.auth.rbac import require_admin, require_manager_or_above

router = APIRouter(prefix="/users", tags=["Users"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


@router.post("/", response_model=UserResponse, status_code=status.HTTP_201_CREATED,
             summary="Create a new user")
async def create_user(
    data: UserCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Admin-only: Create a new user within a tenant."""
    # Check duplicate email
    existing = await db.execute(select(User).where(User.email == data.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        email=data.email,
        full_name=data.full_name,
        phone=data.phone,
        role=data.role,
        tenant_id=data.tenant_id,
        hashed_password=pwd_context.hash(data.password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.get("/", response_model=list[UserResponse], summary="List users in tenant")
async def list_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_manager_or_above),
):
    """List all users within the current user's tenant."""
    result = await db.execute(
        select(User)
        .where(User.tenant_id == current_user.tenant_id)
        .offset(skip)
        .limit(limit)
    )
    return result.scalars().all()


@router.get("/{user_id}", response_model=UserResponse, summary="Get user by ID")
async def get_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    result = await db.execute(
        select(User).where(User.id == user_id, User.tenant_id == current_user.tenant_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.patch("/{user_id}", response_model=UserResponse, summary="Update user")
async def update_user(
    user_id: UUID,
    data: UserUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_manager_or_above),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(user, field, value)

    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Deactivate user")
async def deactivate_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_active = False
    await db.commit()
