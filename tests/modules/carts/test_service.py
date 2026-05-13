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


async def test_cancel_open_cart(db_session):
    from app.modules.carts.models import Cart, CartStatus
    from sqlalchemy import select

    u = await UsersRepository(db_session).create(email="ca@example.com", hashed_password="h")
    await db_session.commit()
    svc = CartsService(CartsRepository(db_session), ItemsRepository(db_session))
    cart = await svc.open_or_get(u.id)
    await db_session.commit()

    cancelled_id = await svc.cancel_my_open_cart(user_id=u.id)
    await db_session.commit()
    assert cancelled_id == cart.id

    row = (await db_session.execute(select(Cart).where(Cart.id == cart.id))).scalar_one()
    assert row.status == CartStatus.cancelled

    # After cancel, partial unique no longer blocks: new open cart can be created.
    new_cart = await svc.open_or_get(u.id)
    await db_session.commit()
    assert new_cart.id != cart.id
    assert new_cart.status == CartStatus.open


async def test_cancel_returns_404_when_no_open(db_session):
    from app.core.exceptions import NotFoundError

    u = await UsersRepository(db_session).create(email="cnone@example.com", hashed_password="h")
    await db_session.commit()
    svc = CartsService(CartsRepository(db_session), ItemsRepository(db_session))

    with pytest.raises(NotFoundError):
        await svc.cancel_my_open_cart(user_id=u.id)


async def test_reopen_restores_failed_timeout_cart(db_session):
    from app.modules.outbox.repository import OutboxRepository
    from app.modules.carts.models import Cart, CartStatus
    from sqlalchemy import select, update as sql_update

    u = await UsersRepository(db_session).create(email="re@example.com", hashed_password="h")
    item = await ItemsRepository(db_session).create(name="X", price_cents=300, currency="JPY")
    await db_session.commit()

    svc = CartsService(
        CartsRepository(db_session),
        ItemsRepository(db_session),
        outbox=OutboxRepository(db_session),
    )
    await svc.add_item(user_id=u.id, item_id=item.id, quantity=2)
    sub = await svc.submit_checkout(user_id=u.id)
    await db_session.commit()
    crid = sub.checkout_request_id

    # Force into failed/timeout (simulate sweeper)
    await db_session.execute(
        sql_update(Cart)
        .where(Cart.checkout_request_id == crid)
        .values(status=CartStatus.failed, failure_reason="timeout")
    )
    await db_session.commit()

    cart = await svc.reopen_my_cart(user_id=u.id)
    await db_session.commit()
    assert cart.status == CartStatus.open
    assert cart.failure_reason is None
    assert cart.submitted_at is None
    assert cart.checkout_request_id is None
    assert cart.order_id is None

    # Lines and unit_price_cents preserved
    lines = await CartsRepository(db_session).list_lines(cart.id)
    assert len(lines) == 1
    assert lines[0].quantity == 2
    assert lines[0].unit_price_cents == 300


async def test_reopen_returns_404_when_no_reopenable(db_session):
    from app.core.exceptions import NotFoundError

    u = await UsersRepository(db_session).create(email="rn@example.com", hashed_password="h")
    await db_session.commit()
    svc = CartsService(CartsRepository(db_session), ItemsRepository(db_session))

    with pytest.raises(NotFoundError):
        await svc.reopen_my_cart(user_id=u.id)


async def test_reopen_returns_404_when_failure_reason_not_timeout(db_session):
    from app.core.exceptions import NotFoundError
    from app.modules.outbox.repository import OutboxRepository
    from app.modules.carts.models import Cart, CartStatus
    from sqlalchemy import update as sql_update

    u = await UsersRepository(db_session).create(email="rnt@example.com", hashed_password="h")
    item = await ItemsRepository(db_session).create(name="X", price_cents=100, currency="JPY")
    await db_session.commit()

    svc = CartsService(
        CartsRepository(db_session),
        ItemsRepository(db_session),
        outbox=OutboxRepository(db_session),
    )
    await svc.add_item(user_id=u.id, item_id=item.id, quantity=1)
    sub = await svc.submit_checkout(user_id=u.id)
    await db_session.commit()

    await db_session.execute(
        sql_update(Cart)
        .where(Cart.checkout_request_id == sub.checkout_request_id)
        .values(status=CartStatus.failed, failure_reason="out_of_stock")
    )
    await db_session.commit()

    with pytest.raises(NotFoundError):
        await svc.reopen_my_cart(user_id=u.id)


async def test_reopen_raises_409_when_open_cart_already_exists(db_session):
    from app.core.exceptions import OpenCartAlreadyExistsError
    from app.modules.outbox.repository import OutboxRepository
    from app.modules.carts.models import Cart, CartStatus
    from sqlalchemy import insert as sql_insert
    import uuid as _uuid

    u = await UsersRepository(db_session).create(email="re2@example.com", hashed_password="h")
    item = await ItemsRepository(db_session).create(name="X", price_cents=200, currency="JPY")
    await db_session.commit()

    # First cart: submit and force to failed/timeout
    svc = CartsService(
        CartsRepository(db_session),
        ItemsRepository(db_session),
        outbox=OutboxRepository(db_session),
    )
    await svc.add_item(user_id=u.id, item_id=item.id, quantity=1)
    sub = await svc.submit_checkout(user_id=u.id)
    await db_session.commit()
    from sqlalchemy import update as sql_update
    await db_session.execute(
        sql_update(Cart)
        .where(Cart.checkout_request_id == sub.checkout_request_id)
        .values(status=CartStatus.failed, failure_reason="timeout")
    )
    await db_session.commit()

    # Second cart: create a fresh open cart for the same user
    await svc.open_or_get(u.id)
    await db_session.commit()

    with pytest.raises(OpenCartAlreadyExistsError):
        await svc.reopen_my_cart(user_id=u.id)
