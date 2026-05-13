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
