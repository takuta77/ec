import uuid
import pytest

from app.modules.outbox.repository import OutboxRepository


pytestmark = [pytest.mark.slow, pytest.mark.asyncio]


async def test_append_fetch_mark(db_session):
    repo = OutboxRepository(db_session)
    row = await repo.append(
        aggregate_type="cart", aggregate_id=uuid.uuid4(),
        event_type="checkout.requested",
        payload={"hello": "world"}, headers={"traceparent": "tp"},
    )
    await db_session.commit()

    rows = await repo.fetch_unpublished(limit=10)
    assert any(r.id == row.id for r in rows)

    await repo.mark_published(row.id)
    await db_session.commit()
    rows2 = await repo.fetch_unpublished(limit=10)
    assert all(r.id != row.id for r in rows2)


async def test_bump_attempts_and_dead_letter(db_session):
    repo = OutboxRepository(db_session)
    row = await repo.append(aggregate_type="cart", aggregate_id=uuid.uuid4(),
                            event_type="checkout.requested", payload={}, headers={})
    await db_session.commit()
    for _ in range(8):
        await repo.bump_attempts(row.id)
        await db_session.commit()
    await repo.mark_dead_letter(row.id)
    await db_session.commit()
    rows = await repo.fetch_unpublished(limit=10)
    assert all(r.id != row.id for r in rows)
