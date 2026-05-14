import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.modules.carts.models import Cart, CartStatus
from app.modules.users.repository import UsersRepository
from app.modules.items.repository import ItemsRepository
from app.modules.carts.repository import CartsRepository
from app.modules.carts.service import CartsService
from app.modules.outbox.repository import OutboxRepository
from app.workers.checkout_sweeper import sweep_once


pytestmark = [pytest.mark.slow, pytest.mark.asyncio]


async def _setup_submitted(db_session, age_hours: float):
    u = await UsersRepository(db_session).create(email=f"{uuid.uuid4()}@x.com", hashed_password="h")
    i = await ItemsRepository(db_session).create(name="X", price_cents=100, currency="JPY")
    svc = CartsService(
        CartsRepository(db_session),
        ItemsRepository(db_session),
        outbox=OutboxRepository(db_session),
    )
    await svc.add_item(user_id=u.id, item_id=i.id, quantity=1)
    sub = await svc.submit_checkout(user_id=u.id)
    new_ts = datetime.now(tz=timezone.utc) - timedelta(hours=age_hours)
    await db_session.execute(
        update(Cart)
        .where(Cart.checkout_request_id == sub.checkout_request_id)
        .values(submitted_at=new_ts)
    )
    await db_session.commit()
    return sub.checkout_request_id


async def test_marks_old_submitted_as_timeout(db_session):
    crid = await _setup_submitted(db_session, age_hours=30)
    count = await sweep_once(db_session, timeout_hours=24)
    await db_session.commit()
    assert count == 1
    cart = (
        await db_session.execute(select(Cart).where(Cart.checkout_request_id == crid))
    ).scalar_one()
    assert cart.status == CartStatus.failed
    assert cart.failure_reason == "timeout"


async def test_skips_fresh_submitted(db_session):
    crid = await _setup_submitted(db_session, age_hours=1)
    count = await sweep_once(db_session, timeout_hours=24)
    await db_session.commit()
    assert count == 0
    cart = (
        await db_session.execute(select(Cart).where(Cart.checkout_request_id == crid))
    ).scalar_one()
    assert cart.status == CartStatus.submitted


async def test_skips_terminal_carts(db_session):
    crid = await _setup_submitted(db_session, age_hours=30)
    await db_session.execute(
        update(Cart).where(Cart.checkout_request_id == crid).values(status=CartStatus.ordered)
    )
    await db_session.commit()
    count = await sweep_once(db_session, timeout_hours=24)
    await db_session.commit()
    assert count == 0
