from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.outbox.models import OutboxEvent


class OutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def append(
        self,
        *,
        aggregate_type: str,
        aggregate_id: uuid.UUID,
        event_type: str,
        payload: dict[str, Any],
        headers: dict[str, Any],
    ) -> OutboxEvent:
        ev = OutboxEvent(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=payload,
            headers=headers,
        )
        self.session.add(ev)
        await self.session.flush()
        return ev

    async def fetch_unpublished(self, *, limit: int = 100) -> list[OutboxEvent]:
        stmt = (
            select(OutboxEvent)
            .where(OutboxEvent.published_at.is_(None), OutboxEvent.dead_letter_at.is_(None))
            .order_by(OutboxEvent.created_at)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def mark_published(self, event_id: uuid.UUID) -> None:
        await self.session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id == event_id)
            .values(published_at=datetime.now(tz=timezone.utc))
        )

    async def bump_attempts(self, event_id: uuid.UUID) -> None:
        await self.session.execute(
            update(OutboxEvent).where(OutboxEvent.id == event_id).values(attempts=OutboxEvent.attempts + 1)
        )

    async def mark_dead_letter(self, event_id: uuid.UUID) -> None:
        await self.session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id == event_id)
            .values(dead_letter_at=datetime.now(tz=timezone.utc))
        )
