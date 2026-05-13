import pytest
from httpx import AsyncClient, ASGITransport


pytestmark = [pytest.mark.slow, pytest.mark.asyncio]


async def _auth(c):
    await c.post("/auth/register", json={"email": "k@example.com", "password": "pw"})
    r = await c.post("/auth/login", json={"email": "k@example.com", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_open_cart_and_add_remove(app_with_db, db_session):
    from app.modules.items.repository import ItemsRepository
    item = await ItemsRepository(db_session).create(name="T", price_cents=100, currency="JPY")
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        h = await _auth(c)
        r = await c.get("/cart", headers=h)
        assert r.status_code == 200 and r.json()["status"] == "open"

        r = await c.post("/cart/items", json={"item_id": str(item.id), "quantity": 2}, headers=h)
        assert r.status_code == 200
        assert r.json()["lines"][0]["quantity"] == 2

        r = await c.delete(f"/cart/items/{item.id}", headers=h)
        assert r.status_code == 200
        assert r.json()["lines"] == []


async def test_cancel_then_new_cart_flow(app_with_db, db_session):
    from app.modules.items.repository import ItemsRepository
    item = await ItemsRepository(db_session).create(name="T", price_cents=100, currency="JPY")
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        await c.post("/auth/register", json={"email": "cancel@example.com", "password": "pw"})
        r = await c.post("/auth/login", json={"email": "cancel@example.com", "password": "pw"})
        h = {"Authorization": f"Bearer {r.json()['access_token']}"}

        await c.post("/cart/items", json={"item_id": str(item.id), "quantity": 1}, headers=h)
        r = await c.post("/cart/cancel", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "cancelled"

        # GET /cart should now return a fresh open cart with no lines
        r = await c.get("/cart", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "open"
        assert body["lines"] == []

        # Second cancel without first having added items still works (still has the open cart)
        r = await c.post("/cart/cancel", headers=h)
        assert r.status_code == 200


async def test_reopen_and_resubmit_flow(app_with_db, db_session):
    from app.modules.items.repository import ItemsRepository
    from app.modules.carts.models import Cart, CartStatus
    from sqlalchemy import update as sql_update

    item = await ItemsRepository(db_session).create(name="T", price_cents=500, currency="JPY")
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        await c.post("/auth/register", json={"email": "reopen@example.com", "password": "pw"})
        r = await c.post("/auth/login", json={"email": "reopen@example.com", "password": "pw"})
        h = {"Authorization": f"Bearer {r.json()['access_token']}"}

        # Setup: add item, checkout
        await c.post("/cart/items", json={"item_id": str(item.id), "quantity": 3}, headers=h)
        r = await c.post("/cart/checkout", headers=h)
        assert r.status_code == 202
        first_crid = r.json()["checkout_request_id"]

        # Simulate timeout: directly mutate the DB
        await db_session.execute(
            sql_update(Cart)
            .where(Cart.checkout_request_id == first_crid)
            .values(status=CartStatus.failed, failure_reason="timeout")
        )
        await db_session.commit()

        # Reopen
        r = await c.post("/cart/reopen", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "open"
        assert body["failure_reason"] is None
        assert body["lines"][0]["quantity"] == 3
        assert body["lines"][0]["unit_price_cents"] == 500

        # Re-checkout yields a new checkout_request_id
        r = await c.post("/cart/checkout", headers=h)
        assert r.status_code == 202
        assert r.json()["checkout_request_id"] != first_crid
