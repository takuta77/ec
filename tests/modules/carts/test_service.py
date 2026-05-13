import pytest

from app.modules.items.repository import ItemsRepository
from app.modules.users.repository import UsersRepository
from app.modules.carts.repository import CartsRepository
from app.modules.carts.service import CartsService
from app.core.exceptions import NotFoundError


pytestmark = [pytest.mark.slow, pytest.mark.asyncio]


async def test_open_add_remove(db_session):
    users = UsersRepository(db_session)
    items_repo = ItemsRepository(db_session)
    u = await users.create(email="c@example.com", hashed_password="h")
    i = await items_repo.create(name="X", price_cents=500, currency="JPY")
    await db_session.commit()

    svc = CartsService(CartsRepository(db_session), items_repo)
    cart = await svc.open_or_get(u.id)
    await db_session.commit()
    assert cart.status.value == "open"

    cart, line = await svc.add_item(user_id=u.id, item_id=i.id, quantity=2)
    await db_session.commit()
    assert line.quantity == 2 and line.unit_price_cents == 500

    cart = await svc.remove_item(user_id=u.id, item_id=i.id)
    await db_session.commit()
    assert cart.status.value == "open"

    with pytest.raises(NotFoundError):
        await svc.remove_item(user_id=u.id, item_id=i.id)
