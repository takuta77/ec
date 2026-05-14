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


async def test_submit_checkout_writes_outbox_and_marks_submitted(db_session):
    from app.modules.outbox.repository import OutboxRepository
    from app.modules.outbox.models import OutboxEvent
    from sqlalchemy import select

    users = UsersRepository(db_session)
    items_repo = ItemsRepository(db_session)
    u = await users.create(email="ch@example.com", hashed_password="h")
    i = await items_repo.create(name="X", price_cents=200, currency="JPY")
    await db_session.commit()

    svc = CartsService(CartsRepository(db_session), items_repo, outbox=OutboxRepository(db_session))
    await svc.add_item(user_id=u.id, item_id=i.id, quantity=3)
    await db_session.commit()

    result = await svc.submit_checkout(user_id=u.id, traceparent="00-aaaa-bbbb-01")
    await db_session.commit()

    assert result.checkout_request_id is not None
    rows = (await db_session.execute(select(OutboxEvent))).scalars().all()
    assert len(rows) == 1
    ev = rows[0]
    assert ev.event_type == "checkout.requested"
    assert ev.payload["data"]["total_cents"] == 600
    assert ev.payload["data"]["items"][0]["quantity"] == 3
    assert ev.headers["traceparent"] == "00-aaaa-bbbb-01"


async def test_apply_order_created_transitions_only_from_submitted(db_session):
    from app.modules.outbox.repository import OutboxRepository

    users = UsersRepository(db_session)
    items_repo = ItemsRepository(db_session)
    u = await users.create(email="a@example.com", hashed_password="h")
    i = await items_repo.create(name="X", price_cents=100, currency="JPY")
    await db_session.commit()

    svc = CartsService(CartsRepository(db_session), items_repo, outbox=OutboxRepository(db_session))
    await svc.add_item(user_id=u.id, item_id=i.id, quantity=1)
    await db_session.commit()
    submission = await svc.submit_checkout(user_id=u.id)
    await db_session.commit()

    import uuid as _uuid

    affected = await svc.apply_order_result(
        event_type="order.created",
        checkout_request_id=submission.checkout_request_id,
        order_id=_uuid.uuid4(),
        failure_reason=None,
    )
    await db_session.commit()
    assert affected == 1

    # Late duplicate / late order.failed: no-op
    affected2 = await svc.apply_order_result(
        event_type="order.failed",
        checkout_request_id=submission.checkout_request_id,
        order_id=None,
        failure_reason="out_of_stock",
    )
    await db_session.commit()
    assert affected2 == 0
