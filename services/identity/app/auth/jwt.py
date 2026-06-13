"""
JWT Authentication — RS256 asymmetric signing.
Identity service holds the private key; all services verify with the public key.

Token structure:
  {
    "sub": "<user_uuid>",
    "tenant_id": "<tenant_uuid>",
    "roles": ["sales_rep"],
    "exp": <unix_timestamp>,
    "iat": <unix_timestamp>,
    "jti": "<uuid>"   # unique token ID for revocation
  }
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import jwt, JWTError

from app.config import get_settings
from app.schemas.schemas import TokenPayload

settings = get_settings()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(
    user_id: str,
    tenant_id: str,
    roles: list[str],
) -> tuple[str, int]:
    """
    Issue a short-lived RS256 access token.
    Returns (token_string, expires_in_seconds).
    """
    expire_minutes = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
    now = _utc_now()
    expire = now + timedelta(minutes=expire_minutes)

    payload: dict[str, Any] = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "roles": roles,
        "exp": expire,
        "iat": now,
        "jti": str(uuid.uuid4()),
    }
    token = jwt.encode(payload, settings.jwt_private_key, algorithm=settings.JWT_ALGORITHM)
    return token, expire_minutes * 60


def create_refresh_token(user_id: str) -> tuple[str, str]:
    """
    Issue a long-lived refresh token (opaque UUID stored in Redis).
    Returns (jti, token_string). The token_string is just the jti.
    """
    jti = str(uuid.uuid4())
    return jti, jti  # Refresh token is the JTI itself; stored in Redis


def decode_access_token(token: str) -> TokenPayload:
    """
    Decode and validate an RS256 JWT.
    Raises JWTError on invalid/expired tokens.
    """
    payload = jwt.decode(
        token,
        settings.jwt_public_key,
        algorithms=[settings.JWT_ALGORITHM],
    )
    return TokenPayload(**payload)
