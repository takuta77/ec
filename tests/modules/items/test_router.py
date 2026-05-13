import pytest
from httpx import AsyncClient, ASGITransport


pytestmark = [pytest.mark.slow, pytest.mark.asyncio]


async def test_list_items_empty(app_with_db, db_session):
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/items")
        assert r.status_code == 200
        assert r.json() == []
