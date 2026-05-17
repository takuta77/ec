from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update as sql_update

from app.modules.carts.models import Cart, CartItem, CartStatus
from app.modules.items.repository import ItemsRepository
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


async def test_admin_list_carts_no_filter(app_with_db, db_session) -> None:
    user = await UsersRepository(db_session).create(
        email=f"u-{uuid.uuid4().hex[:8]}@example.com", hashed_password="hash"
    )
    db_session.add_all(
        [
            Cart(user_id=user.id, status=CartStatus.open),
            Cart(user_id=user.id, status=CartStatus.ordered),
            Cart(user_id=user.id, status=CartStatus.failed, failure_reason="timeout"),
        ]
    )
    await db_session.commit()

    headers = await _seed_admin(db_session, app_with_db)
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/admin/carts", headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 3
    statuses = sorted(d["status"] for d in data)
    assert statuses == ["failed", "open", "ordered"]


async def test_admin_list_carts_status_filter(app_with_db, db_session) -> None:
    user = await UsersRepository(db_session).create(
        email=f"u-{uuid.uuid4().hex[:8]}@example.com", hashed_password="hash"
    )
    db_session.add_all(
        [
            Cart(user_id=user.id, status=CartStatus.failed, failure_reason="timeout"),
            Cart(user_id=user.id, status=CartStatus.ordered),
        ]
    )
    await db_session.commit()

    headers = await _seed_admin(db_session, app_with_db)
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/admin/carts", params={"status": "failed"}, headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["status"] == "failed"
    assert data[0]["failure_reason"] == "timeout"


async def test_admin_list_carts_line_count(app_with_db, db_session) -> None:
    user = await UsersRepository(db_session).create(
        email=f"u-{uuid.uuid4().hex[:8]}@example.com", hashed_password="hash"
    )
    items_repo = ItemsRepository(db_session)
    item_a = await items_repo.create(name="A", price_cents=100, currency="JPY")
    item_b = await items_repo.create(name="B", price_cents=200, currency="JPY")

    cart = Cart(user_id=user.id, status=CartStatus.open)
    db_session.add(cart)
    await db_session.flush()
    db_session.add_all(
        [
            CartItem(cart_id=cart.id, item_id=item_a.id, quantity=1, unit_price_cents=100),
            CartItem(cart_id=cart.id, item_id=item_b.id, quantity=3, unit_price_cents=200),
        ]
    )
    await db_session.commit()

    headers = await _seed_admin(db_session, app_with_db)
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/admin/carts", params={"status": "open"}, headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["line_count"] == 2
