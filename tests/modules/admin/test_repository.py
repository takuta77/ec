from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import update as sql_update

from app.modules.admin.repository import AdminRepository
from app.modules.carts.models import Cart, CartItem, CartStatus
from app.modules.items.models import Item as ItemModel
from app.modules.items.repository import ItemsRepository
from app.modules.outbox.models import OutboxEvent
from app.modules.outbox.repository import OutboxRepository
from app.modules.users.repository import UsersRepository


pytestmark = [pytest.mark.slow, pytest.mark.asyncio]


async def _seed_user(db_session):
    return await UsersRepository(db_session).create(
        email=f"u-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="hash",
    )


async def test_items_stats(db_session) -> None:
    repo = AdminRepository(db_session)
    items_repo = ItemsRepository(db_session)
    await items_repo.create(name="A", price_cents=100, currency="JPY", category="beverages")
    await items_repo.create(name="B", price_cents=200, currency="JPY", category="beverages")
    await items_repo.create(name="C", price_cents=300, currency="JPY", category="stationery")
    await items_repo.create(name="D", price_cents=400, currency="JPY")  # no category
    inactive = await items_repo.create(
        name="E", price_cents=500, currency="JPY", category="stationery"
    )
    await db_session.execute(
        sql_update(ItemModel).where(ItemModel.id == inactive.id).values(is_active=False)
    )
    await db_session.commit()

    total, active, by_category = await repo.items_stats()
    assert total == 5
    assert active == 4
    assert by_category == {"beverages": 2, "stationery": 1}


async def test_carts_stats(db_session) -> None:
    repo = AdminRepository(db_session)
    user = await _seed_user(db_session)
    db_session.add_all(
        [
            Cart(user_id=user.id, status=CartStatus.open),
            Cart(user_id=user.id, status=CartStatus.submitted),
            Cart(user_id=user.id, status=CartStatus.failed, failure_reason="timeout"),
            Cart(user_id=user.id, status=CartStatus.failed, failure_reason="downstream_error"),
            Cart(user_id=user.id, status=CartStatus.cancelled),
            Cart(user_id=user.id, status=CartStatus.ordered),
        ]
    )
    await db_session.commit()

    by_status, failed_timeout = await repo.carts_stats()
    assert by_status == {"open": 1, "submitted": 1, "ordered": 1, "failed": 2, "cancelled": 1}
    assert failed_timeout == 1


async def test_carts_stats_empty(db_session) -> None:
    repo = AdminRepository(db_session)
    by_status, failed_timeout = await repo.carts_stats()
    assert by_status == {"open": 0, "submitted": 0, "ordered": 0, "failed": 0, "cancelled": 0}
    assert failed_timeout == 0


async def test_outbox_stats(db_session) -> None:
    repo = AdminRepository(db_session)
    outbox = OutboxRepository(db_session)

    # 2 pending, 1 published.
    aggregate_id = uuid.uuid4()
    await outbox.append(
        aggregate_type="cart",
        aggregate_id=aggregate_id,
        event_type="ec.order.completed",
        payload={"x": 1},
        headers={},
    )
    await outbox.append(
        aggregate_type="cart",
        aggregate_id=aggregate_id,
        event_type="ec.order.completed",
        payload={"x": 2},
        headers={},
    )
    e3 = await outbox.append(
        aggregate_type="cart",
        aggregate_id=aggregate_id,
        event_type="ec.order.completed",
        payload={"x": 3},
        headers={},
    )
    await db_session.execute(
        sql_update(OutboxEvent)
        .where(OutboxEvent.id == e3.id)
        .values(published_at=datetime.now(tz=timezone.utc))
    )
    await db_session.commit()

    pending, dispatched, oldest = await repo.outbox_stats()
    assert pending == 2
    assert dispatched == 1
    assert oldest is not None
    assert isinstance(oldest, datetime)


async def test_outbox_stats_excludes_dead_letter_from_pending(db_session) -> None:
    repo = AdminRepository(db_session)
    outbox = OutboxRepository(db_session)
    aggregate_id = uuid.uuid4()
    e1 = await outbox.append(
        aggregate_type="cart",
        aggregate_id=aggregate_id,
        event_type="ec.order.completed",
        payload={"x": 1},
        headers={},
    )
    # Mark dead-lettered (not published).
    await db_session.execute(
        sql_update(OutboxEvent)
        .where(OutboxEvent.id == e1.id)
        .values(dead_letter_at=datetime.now(tz=timezone.utc))
    )
    await db_session.commit()

    pending, dispatched, oldest = await repo.outbox_stats()
    assert pending == 0
    assert dispatched == 0
    assert oldest is None


async def test_list_carts_no_filter(db_session) -> None:
    repo = AdminRepository(db_session)
    user = await _seed_user(db_session)
    db_session.add_all(
        [
            Cart(user_id=user.id, status=CartStatus.open),
            Cart(user_id=user.id, status=CartStatus.ordered),
            Cart(user_id=user.id, status=CartStatus.failed, failure_reason="timeout"),
        ]
    )
    await db_session.commit()

    rows = await repo.list_carts(status=None, limit=10, offset=0)
    assert len(rows) == 3
    assert all(r.line_count == 0 for r in rows)


async def test_list_carts_status_filter(db_session) -> None:
    repo = AdminRepository(db_session)
    user = await _seed_user(db_session)
    db_session.add_all(
        [
            Cart(user_id=user.id, status=CartStatus.failed, failure_reason="timeout"),
            Cart(user_id=user.id, status=CartStatus.ordered),
        ]
    )
    await db_session.commit()

    rows = await repo.list_carts(status="failed", limit=10, offset=0)
    assert len(rows) == 1
    assert rows[0].status == "failed"
    assert rows[0].failure_reason == "timeout"


async def test_list_carts_line_count(db_session) -> None:
    repo = AdminRepository(db_session)
    user = await _seed_user(db_session)
    items_repo = ItemsRepository(db_session)
    item_a = await items_repo.create(name="A", price_cents=100, currency="JPY")
    item_b = await items_repo.create(name="B", price_cents=200, currency="JPY")

    cart = Cart(user_id=user.id, status=CartStatus.open)
    db_session.add(cart)
    await db_session.flush()
    db_session.add_all(
        [
            CartItem(cart_id=cart.id, item_id=item_a.id, quantity=1, unit_price_cents=100),
            CartItem(cart_id=cart.id, item_id=item_b.id, quantity=3, unit_price_cents=200),
        ]
    )
    await db_session.commit()

    rows = await repo.list_carts(status="open", limit=10, offset=0)
    assert len(rows) == 1
    assert rows[0].line_count == 2
