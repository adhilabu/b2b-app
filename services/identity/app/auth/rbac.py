"""
RBAC — Role-Based Access Control dependency decorators.
"""
from typing import Callable
from fastapi import Depends, HTTPException, status
from app.auth.dependencies import get_current_user
from app.models.user import User, UserRole


def require_roles(*allowed_roles: UserRole) -> Callable:
    """
    FastAPI dependency factory. Raises 403 if the current user's role
    is not in the allowed_roles list.

    Usage:
        @router.get("/admin-only")
        async def admin_endpoint(
            current_user: User = Depends(require_roles(UserRole.admin))
        ):
    """
    async def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{current_user.role}' is not authorized for this action. "
                       f"Required: {[r.value for r in allowed_roles]}",
            )
        return current_user

    return role_checker


# Convenience shortcuts
require_admin = require_roles(UserRole.admin)
require_manager_or_above = require_roles(UserRole.admin, UserRole.manager)
require_supervisor_or_above = require_roles(UserRole.admin, UserRole.manager, UserRole.supervisor)
