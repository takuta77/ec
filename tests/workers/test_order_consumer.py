import uuid

import pytest

from app.modules.carts.repository import CartsRepository
from app.modules.carts.service import CartsService
from app.modules.items.repository import ItemsRepository
from app.modules.outbox.repository import OutboxRepository
from app.modules.users.repository import UsersRepository
from app.workers.order_consumer import handle_event


# NOTE: The in-process tests below call `handle_event(session, envelope)` directly,
# so they do not need a RabbitMQ container. The end-to-end pipeline test that
# actually exercises the consumer loop (publish -> consume) lives in Task 26.
# We deliberately omit the `rabbit` / `amqp_url` fixtures here to keep this
# suite fast and free of unused-fixture warnings.
pytestmark = [pytest.mark.slow, pytest.mark.asyncio]


async def _setup_submitted_cart(db_session) -> uuid.UUID:
    users = UsersRepository(db_session)
    items_repo = ItemsRepository(db_session)
    u = await users.create(email=f"{uuid.uuid4()}@x.com", hashed_password="h")
    i = await items_repo.create(name="X", price_cents=100, currency="JPY")
    svc = CartsService(CartsRepository(db_session), items_repo, outbox=OutboxRepository(db_session))
    await svc.add_item(user_id=u.id, item_id=i.id, quantity=1)
    sub = await svc.submit_checkout(user_id=u.id)
    await db_session.commit()
    return sub.checkout_request_id


async def test_handle_order_created_transitions_cart(db_session):
    crid = await _setup_submitted_cart(db_session)
    envelope = {
        "event_id": str(uuid.uuid4()),
        "event_type": "order.created",
        "data": {
            "checkout_request_id": str(crid),
            "order_id": str(uuid.uuid4()),
            "status": "confirmed",
            "confirmed_at": "2026-05-12T00:00:00Z",
        },
    }
    await handle_event(db_session, envelope)
    await db_session.commit()

    from sqlalchemy import select

    from app.modules.carts.models import Cart, CartStatus

    cart = (
        await db_session.execute(select(Cart).where(Cart.checkout_request_id == crid))
    ).scalar_one()
    assert cart.status == CartStatus.ordered


async def test_handle_is_idempotent_on_same_event_id(db_session):
    crid = await _setup_submitted_cart(db_session)
    same_id = str(uuid.uuid4())
    envelope = {
        "event_id": same_id,
        "event_type": "order.created",
        "data": {
            "checkout_request_id": str(crid),
            "order_id": str(uuid.uuid4()),
            "status": "confirmed",
            "confirmed_at": "2026-05-12T00:00:00Z",
        },
    }
    await handle_event(db_session, envelope)
    await db_session.commit()
    await handle_event(db_session, envelope)
    await db_session.commit()
    from sqlalchemy import select

    from app.modules.carts.models import Cart, CartStatus

    cart = (
        await db_session.execute(select(Cart).where(Cart.checkout_request_id == crid))
    ).scalar_one()
    assert cart.status == CartStatus.ordered


async def test_handle_late_failed_after_created_is_noop(db_session):
    crid = await _setup_submitted_cart(db_session)
    created = {
        "event_id": str(uuid.uuid4()),
        "event_type": "order.created",
        "data": {
            "checkout_request_id": str(crid),
            "order_id": str(uuid.uuid4()),
            "status": "confirmed",
            "confirmed_at": "2026-05-12T00:00:00Z",
        },
    }
    failed = {
        "event_id": str(uuid.uuid4()),
        "event_type": "order.failed",
        "data": {"checkout_request_id": str(crid), "reason": "out_of_stock"},
    }
    await handle_event(db_session, created)
    await db_session.commit()
    await handle_event(db_session, failed)
    await db_session.commit()
    from sqlalchemy import select

    from app.modules.carts.models import Cart, CartStatus

    cart = (
        await db_session.execute(select(Cart).where(Cart.checkout_request_id == crid))
    ).scalar_one()
    assert cart.status == CartStatus.ordered


async def test_poison_message_raises_for_consumer_to_dlq(db_session):
    with pytest.raises(KeyError):
        await handle_event(db_session, {"event_id": "x"})  # missing required fields
