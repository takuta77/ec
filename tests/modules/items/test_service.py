import pytest

from app.modules.items.repository import ItemsRepository
from app.modules.items.service import ItemsService


pytestmark = [pytest.mark.slow, pytest.mark.asyncio]


async def test_create_list_get(db_session):
    svc = ItemsService(ItemsRepository(db_session))
    a = await svc.create(name="Widget", price_cents=1980, currency="JPY")
    await db_session.commit()
    found = await svc.get(a.id)
    assert found.name == "Widget"
    page = await svc.list_active(limit=10, offset=0)
    assert len(page) == 1
