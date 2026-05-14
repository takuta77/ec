from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.exceptions import AuthorizationError
from app.modules.auth.dependencies import get_current_user
from app.modules.users.models import User


async def require_admin(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """FastAPI dependency that allows only admin users.

    Used as `Depends(require_admin)` on routes under `/admin/*`.
    """
    if not user.is_admin:
        raise AuthorizationError(
            "Admin privileges required",
            details={"user_id": str(user.id)},
        )
    return user
