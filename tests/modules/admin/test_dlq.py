from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update as sql_update

from app.modules.users.models import User


pytestmark = [pytest.mark.slow, pytest.mark.asyncio]


async def _seed_admin(db_session, app_with_db) -> dict[str, str]:
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        email = f"admin-{uuid.uuid4().hex[:8]}@example.com"
        await c.post("/auth/register", json={"email": email, "password": "pw"})
        r = await c.post("/auth/login", json={"email": email, "password": "pw"})
        token = r.json()["access_token"]
    await db_session.execute(sql_update(User).where(User.email == email).values(is_admin=True))
    await db_session.commit()
    return {"Authorization": f"Bearer {token}"}


async def test_peek_dlq_when_mq_unavailable_returns_503(app_with_db, db_session) -> None:
    """In test fixture environment, lifespan doesn't always run, so mq_connection
    is None and peek returns 503. This is the documented behavior."""
    headers = await _seed_admin(db_session, app_with_db)
    queue = f"ec.test_admin_peek_{uuid.uuid4().hex[:8]}"
    mq_conn = getattr(app_with_db.state, "mq_connection", None)
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get(f"/admin/dlq/{queue}/peek", headers=headers)
    if mq_conn is None:
        assert r.status_code == 503
    else:
        # With MQ available, peek on a non-existent queue should 404.
        assert r.status_code == 404
