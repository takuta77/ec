import uuid

import pytest

from app.modules.users.repository import UsersRepository
from app.modules.auth.repository import RefreshTokensRepository


pytestmark = [pytest.mark.slow, pytest.mark.asyncio]


async def test_store_and_find_refresh_token(db_session):
    users = UsersRepository(db_session)
    user = await users.create(email="r@example.com", hashed_password="h")
    await db_session.flush()

    repo = RefreshTokensRepository(db_session)
    expires_at = "2030-01-01T00:00:00+00:00"
    rt = await repo.store(user_id=user.id, token_hash="hashed-jti", expires_at=expires_at)
    await db_session.commit()

    found = await repo.find_active_by_hash("hashed-jti")
    assert found is not None and found.user_id == user.id

    await repo.revoke(rt.id)
    await db_session.commit()
    assert await repo.find_active_by_hash("hashed-jti") is None
