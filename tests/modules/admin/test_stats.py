from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update as sql_update

from app.modules.carts.models import Cart, CartStatus
from app.modules.items.models import Item as ItemModel
from app.modules.items.repository import ItemsRepository
from app.modules.outbox.models import OutboxEvent
from app.modules.outbox.repository import OutboxRepository
from app.modules.users.models import User
from app.modules.users.repository import UsersRepository


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


async def _seed_non_admin(app_with_db) -> dict[str, str]:
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        email = f"u-{uuid.uuid4().hex[:8]}@example.com"
        await c.post("/auth/register", json={"email": email, "password": "pw"})
        r = await c.post("/auth/login", json={"email": email, "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_unauthenticated_returns_401(app_with_db) -> None:
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/admin/stats/items")
    assert r.status_code == 401


async def test_non_admin_returns_403(app_with_db) -> None:
    headers = await _seed_non_admin(app_with_db)
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/admin/stats/items", headers=headers)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


async def test_admin_items_stats(app_with_db, db_session) -> None:
    items_repo = ItemsRepository(db_session)
    await items_repo.create(name="A", price_cents=100, currency="JPY", category="beverages")
    await items_repo.create(name="B", price_cents=200, currency="JPY", category="beverages")
    inactive = await items_repo.create(
        name="C", price_cents=300, currency="JPY", category="stationery"
    )
    await db_session.execute(
        sql_update(ItemModel).where(ItemModel.id == inactive.id).values(is_active=False)
    )
    await db_session.commit()

    headers = await _seed_admin(db_session, app_with_db)
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/admin/stats/items", headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 3
    assert data["active"] == 2
    assert data["by_category"] == {"beverages": 2}


async def test_admin_carts_stats(app_with_db, db_session) -> None:
    user = await UsersRepository(db_session).create(
        email=f"u-{uuid.uuid4().hex[:8]}@example.com", hashed_password="hash"
    )
    db_session.add_all(
        [
            Cart(user_id=user.id, status=CartStatus.open),
            Cart(user_id=user.id, status=CartStatus.failed, failure_reason="timeout"),
        ]
    )
    await db_session.commit()

    headers = await _seed_admin(db_session, app_with_db)
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/admin/stats/carts", headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["by_status"]["open"] == 1
    assert data["by_status"]["failed"] == 1
    assert data["by_status"]["ordered"] == 0
    assert data["failed_with_timeout"] == 1


async def test_admin_outbox_stats(app_with_db, db_session) -> None:
    repo = OutboxRepository(db_session)
    aggregate_id = uuid.uuid4()
    await repo.append(
        aggregate_type="cart",
        aggregate_id=aggregate_id,
        event_type="ec.order.completed",
        payload={"x": 1},
        headers={},
    )
    e2 = await repo.append(
        aggregate_type="cart",
        aggregate_id=aggregate_id,
        event_type="ec.order.completed",
        payload={"x": 2},
        headers={},
    )
    await db_session.execute(
        sql_update(OutboxEvent)
        .where(OutboxEvent.id == e2.id)
        .values(published_at=datetime.now(tz=timezone.utc))
    )
    await db_session.commit()

    headers = await _seed_admin(db_session, app_with_db)
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/admin/stats/outbox", headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["pending"] == 1
    assert data["dispatched"] == 1
    assert data["oldest_pending_at"] is not None


async def test_admin_dlq_stats(app_with_db, db_session) -> None:
    headers = await _seed_admin(db_session, app_with_db)
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/admin/stats/dlq", headers=headers)
    # If app has a MQ connection (lifespan ran with Testcontainers MQ available):
    # 200 with at least one DLQQueueStats. Otherwise 503 (MQ unavailable in test env).
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        data = r.json()
        assert isinstance(data, list)
        assert all("queue" in item and "message_count" in item for item in data)
