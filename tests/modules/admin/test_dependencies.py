from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.core.exceptions import AuthorizationError
from app.modules.admin.dependencies import require_admin
from app.modules.users.models import User


def _build_user(*, is_admin: bool) -> User:
    return User(
        id=uuid.uuid4(),
        email=f"u-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="hash",
        is_admin=is_admin,
        created_at=datetime.now(tz=timezone.utc),
        updated_at=datetime.now(tz=timezone.utc),
    )


@pytest.mark.asyncio
async def test_require_admin_allows_admin() -> None:
    admin = _build_user(is_admin=True)
    result = await require_admin(user=admin)
    assert result is admin


@pytest.mark.asyncio
async def test_require_admin_rejects_non_admin() -> None:
    non_admin = _build_user(is_admin=False)
    with pytest.raises(AuthorizationError) as exc_info:
        await require_admin(user=non_admin)
    assert exc_info.value.code == "forbidden"
    assert exc_info.value.http_status == 403
