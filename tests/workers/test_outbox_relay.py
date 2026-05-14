import json
import uuid

import pytest
from testcontainers.rabbitmq import RabbitMqContainer

from app.mq.connection import open_connection
from app.modules.outbox.repository import OutboxRepository
from app.workers.outbox_relay import relay_once


pytestmark = [pytest.mark.slow, pytest.mark.asyncio]


@pytest.fixture(scope="module")
def rabbit():
    with RabbitMqContainer("rabbitmq:3.13-management") as r:
        yield r


@pytest.fixture(scope="module")
def amqp_url(rabbit):
    p = rabbit.get_exposed_port(5672)
    return f"amqp://guest:guest@127.0.0.1:{p}/"


async def test_relay_publishes_and_marks(db_session, amqp_url):
    repo = OutboxRepository(db_session)
    ev = await repo.append(
        aggregate_type="cart",
        aggregate_id=uuid.uuid4(),
        event_type="checkout.requested",
        payload={"event_id": "e-1", "data": {}},
        headers={"traceparent": "00-tp"},
    )
    await db_session.commit()

    conn = await open_connection(amqp_url)
    chan = await conn.channel()
    ex = await chan.declare_exchange("ec.events", "topic", durable=True)
    queue = await chan.declare_queue("test-relay", durable=False, auto_delete=True)
    await queue.bind(ex, routing_key="checkout.requested")

    published = await relay_once(db_session, conn, exchange="ec.events")
    assert published == 1

    msg = await queue.get(timeout=5)
    assert msg is not None
    body = json.loads(msg.body)
    assert body["event_id"] == "e-1"
    assert msg.headers.get("traceparent") == "00-tp"

    await db_session.commit()
    await db_session.refresh(ev)
    assert ev.published_at is not None
    await conn.close()
