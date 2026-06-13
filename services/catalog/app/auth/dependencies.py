"""
Shared JWT verification dependency for downstream services.
These services only VERIFY tokens (public key), never issue them.
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from pydantic import BaseModel
from app.config import get_settings

settings = get_settings()
bearer_scheme = HTTPBearer()


class TokenPayload(BaseModel):
    sub: str
    tenant_id: str
    roles: list[str]
    exp: int
    iat: int
    jti: str


async def get_current_active_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> TokenPayload:
    """Verify JWT signature using public key and return decoded payload."""
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_public_key,
            algorithms=[settings.JWT_ALGORITHM],
        )
        return TokenPayload(**payload)
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_roles(*roles: str):
    """Role check dependency for downstream services."""
    async def checker(payload: TokenPayload = Depends(get_current_active_user)) -> TokenPayload:
        if not any(r in payload.roles for r in roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required: {list(roles)}",
            )
        return payload
    return checker
