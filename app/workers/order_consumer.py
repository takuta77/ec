from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import build_engine, build_session_factory
from app.mq.connection import open_connection
from app.mq.consumer import Consumer, Envelope
from app.mq.retry import declare_consumer_topology
from app.modules.carts.repository import CartsRepository
from app.modules.carts.service import CartsService
from app.modules.items.repository import ItemsRepository
from app.modules.outbox.processed import ProcessedEventsRepository
from app.modules.outbox.repository import OutboxRepository

logger = logging.getLogger(__name__)
QUEUE_NAME = "ec.api.order-events"
ROUTING_KEYS = ["order.created", "order.failed"]


async def handle_event(session: AsyncSession, envelope: dict[str, Any]) -> None:
    # Validate required envelope keys first so malformed messages raise
    # KeyError (a non-retryable error the Consumer routes to the DLQ)
    # before any UUID parsing.
    raw_event_id = envelope["event_id"]
    event_type = envelope["event_type"]
    data = envelope["data"]
    raw_crid = data["checkout_request_id"]

    event_id = uuid.UUID(raw_event_id)
    crid = uuid.UUID(raw_crid)
    order_id = uuid.UUID(data["order_id"]) if data.get("order_id") else None
    reason = data.get("reason")

    processed = ProcessedEventsRepository(session)
    if not await processed.try_insert(event_id, event_type):
        return
    svc = CartsService(
        CartsRepository(session),
        ItemsRepository(session),
        outbox=OutboxRepository(session),
    )
    await svc.apply_order_result(
        event_type=event_type,
        checkout_request_id=crid,
        order_id=order_id,
        failure_reason=reason,
    )


async def run() -> None:
    settings = get_settings()
    from app.core.telemetry import init_telemetry

    init_telemetry(service_name="ec-order-consumer")
    engine = build_engine(settings.database_url)
    factory = build_session_factory(engine)
    conn = await open_connection(settings.rabbitmq_url)
    await declare_consumer_topology(conn, queue=QUEUE_NAME, routing_keys=ROUTING_KEYS)
    consumer = Consumer(conn, queue_name=QUEUE_NAME, max_retries=settings.max_consumer_retries)

    async def _handler(env: Envelope) -> None:
        async with factory() as session:
            async with session.begin():
                await handle_event(
                    session,
                    {
                        "event_id": env.event_id,
                        "event_type": env.event_type,
                        "data": env.data,
                    },
                )

    try:
        await consumer.consume(_handler)
    finally:
        await conn.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
