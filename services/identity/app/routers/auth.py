"""
Auth Router — Login, Refresh, Logout, Whoami
"""
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from passlib.context import CryptContext
import redis.asyncio as aioredis

from app.database import get_db
from app.config import get_settings
from app.models.user import User
from app.schemas.schemas import LoginRequest, TokenResponse, RefreshRequest, UserResponse
from app.auth.jwt import create_access_token, create_refresh_token
from app.auth.dependencies import get_current_active_user

router = APIRouter(prefix="/auth", tags=["Authentication"])
settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_redis():
    return aioredis.from_url(settings.REDIS_URL, decode_responses=True)


@router.post("/login", response_model=TokenResponse, summary="Login and obtain JWT tokens")
async def login(
    data: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Authenticates a user and returns a short-lived access token (RS256)
    and a long-lived refresh token stored in Redis.
    """
    # Fetch user
    result = await db.execute(
        select(User).where(User.email == data.email, User.is_active == True)
    )
    user = result.scalar_one_or_none()

    if not user or not pwd_context.verify(data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token, expires_in = create_access_token(
        user_id=str(user.id),
        tenant_id=str(user.tenant_id),
        roles=[user.role.value],
    )
    jti, refresh_token = create_refresh_token(str(user.id))

    # Store refresh token in Redis with TTL
    redis_client = get_redis()
    await redis_client.setex(
        f"refresh:{jti}",
        int(timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS).total_seconds()),
        str(user.id),
    )
    await redis_client.aclose()

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
    )


@router.post("/refresh", response_model=TokenResponse, summary="Refresh access token")
async def refresh_token(
    data: RefreshRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Accepts a refresh token (JTI), validates it against Redis,
    and issues a new access token + rotated refresh token.
    """
    redis_client = get_redis()
    user_id = await redis_client.get(f"refresh:{data.refresh_token}")

    if not user_id:
        await redis_client.aclose()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    # Delete old refresh token (rotation)
    await redis_client.delete(f"refresh:{data.refresh_token}")

    result = await db.execute(
        select(User).where(User.id == user_id, User.is_active == True)
    )
    user = result.scalar_one_or_none()
    if not user:
        await redis_client.aclose()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    access_token, expires_in = create_access_token(
        user_id=str(user.id),
        tenant_id=str(user.tenant_id),
        roles=[user.role.value],
    )
    jti, new_refresh_token = create_refresh_token(str(user.id))
    await redis_client.setex(
        f"refresh:{jti}",
        int(timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS).total_seconds()),
        str(user.id),
    )
    await redis_client.aclose()

    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
        expires_in=expires_in,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Logout — revoke refresh token")
async def logout(
    data: RefreshRequest,
):
    """Revoke the refresh token (removes from Redis)."""
    redis_client = get_redis()
    await redis_client.delete(f"refresh:{data.refresh_token}")
    await redis_client.aclose()


@router.get("/me", response_model=UserResponse, summary="Get current user profile")
async def get_me(current_user: User = Depends(get_current_active_user)):
    return current_user
