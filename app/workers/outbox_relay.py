from __future__ import annotations

import asyncio
import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession
import aio_pika

from app.core.config import get_settings
from app.db.session import build_engine, build_session_factory
from app.mq.connection import open_connection
from app.mq.publisher import Publisher
from app.modules.outbox.repository import OutboxRepository


MAX_ATTEMPTS = 8
logger = logging.getLogger(__name__)


async def relay_once(session: AsyncSession, connection: aio_pika.abc.AbstractRobustConnection, *, exchange: str) -> int:
    repo = OutboxRepository(session)
    publisher = Publisher(connection, exchange=exchange)
    await publisher.declare()

    rows = await repo.fetch_unpublished(limit=100)
    published_count = 0
    for row in rows:
        body = json.dumps(row.payload).encode()
        headers = row.headers or {}
        try:
            await publisher.publish(routing_key=row.event_type, body=body, headers=headers)
            await repo.mark_published(row.id)
            published_count += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("publish failed: %s", exc)
            await repo.bump_attempts(row.id)
            if (row.attempts + 1) >= MAX_ATTEMPTS:
                await repo.mark_dead_letter(row.id)
    return published_count


async def run() -> None:
    settings = get_settings()
    engine = build_engine(settings.database_url)
    factory = build_session_factory(engine)
    conn = await open_connection(settings.rabbitmq_url)
    try:
        while True:
            async with factory() as session:
                async with session.begin():
                    n = await relay_once(session, conn, exchange="ec.events")
            await asyncio.sleep(0.2 if n else 1.0)
    finally:
        await conn.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
