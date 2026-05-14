from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


pytestmark = [pytest.mark.slow, pytest.mark.asyncio]


async def _seed(
    db_session,
    name: str,
    *,
    description: str | None = None,
    category: str | None = None,
    is_active: bool = True,
    price_cents: int = 100,
    currency: str = "JPY",
):
    from app.modules.items.repository import ItemsRepository
    from sqlalchemy import update as sql_update
    from app.modules.items.models import Item

    item = await ItemsRepository(db_session).create(
        name=name,
        description=description,
        price_cents=price_cents,
        currency=currency,
        category=category,
    )
    if not is_active:
        await db_session.execute(sql_update(Item).where(Item.id == item.id).values(is_active=False))
    await db_session.commit()
    return item


async def test_q_matches_name(app_with_db, db_session):
    await _seed(db_session, "Apple Juice")
    await _seed(db_session, "Green Tea")
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/items", params={"q": "juice"})
    assert r.status_code == 200
    names = [it["name"] for it in r.json()]
    assert names == ["Apple Juice"]


async def test_q_matches_description(app_with_db, db_session):
    await _seed(db_session, "ABC", description="organic green tea blend")
    await _seed(db_session, "XYZ", description="instant coffee")
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/items", params={"q": "green"})
    assert r.status_code == 200
    names = [it["name"] for it in r.json()]
    assert names == ["ABC"]


async def test_q_no_match_returns_empty(app_with_db, db_session):
    await _seed(db_session, "Apple Juice")
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/items", params={"q": "absolutely-not-present"})
    assert r.status_code == 200
    assert r.json() == []


async def test_q_wildcard_is_literal(app_with_db, db_session):
    await _seed(db_session, "50% off bundle")
    await _seed(db_session, "Plain bundle")
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r1 = await c.get("/items", params={"q": "50%"})
        r2 = await c.get("/items", params={"q": "%"})
    assert r1.status_code == 200 and [it["name"] for it in r1.json()] == ["50% off bundle"]
    assert r2.status_code == 200 and [it["name"] for it in r2.json()] == ["50% off bundle"]


async def test_category_filter(app_with_db, db_session):
    await _seed(db_session, "Apple Juice", category="beverages")
    await _seed(db_session, "Green Tea", category="beverages")
    await _seed(db_session, "Notebook", category="stationery")
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/items", params={"category": "beverages"})
    assert r.status_code == 200
    names = sorted(it["name"] for it in r.json())
    assert names == ["Apple Juice", "Green Tea"]


async def test_q_and_category_combined(app_with_db, db_session):
    await _seed(db_session, "Apple Juice", category="beverages")
    await _seed(db_session, "Apple Pen", category="stationery")
    await _seed(db_session, "Green Tea", category="beverages")
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/items", params={"q": "apple", "category": "beverages"})
    assert r.status_code == 200
    names = [it["name"] for it in r.json()]
    assert names == ["Apple Juice"]


async def test_inactive_items_excluded(app_with_db, db_session):
    await _seed(db_session, "Old Item", category="beverages", is_active=False)
    await _seed(db_session, "New Item", category="beverages")
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/items", params={"category": "beverages"})
    assert r.status_code == 200
    names = [it["name"] for it in r.json()]
    assert names == ["New Item"]


async def test_categories_distinct_sorted(app_with_db, db_session):
    await _seed(db_session, "A", category="beverages")
    await _seed(db_session, "B", category="beverages")
    await _seed(db_session, "C", category="stationery")
    await _seed(db_session, "D", category=None)
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/items/categories")
    assert r.status_code == 200
    assert r.json() == {"categories": ["beverages", "stationery"]}


async def test_categories_empty(app_with_db, db_session):
    await _seed(db_session, "Uncategorized")
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/items/categories")
    assert r.status_code == 200
    assert r.json() == {"categories": []}


async def test_empty_q_returns_422(app_with_db, db_session):
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/items", params={"q": ""})
    assert r.status_code == 422
