import asyncio
import json
import uuid

import aio_pika
import pytest
from testcontainers.rabbitmq import RabbitMqContainer

from app.mq.connection import open_connection
from app.mq.consumer import Consumer, Envelope
from app.mq.retry import MAIN_EXCHANGE, declare_consumer_topology

pytestmark = [pytest.mark.slow, pytest.mark.asyncio]


@pytest.fixture(scope="module")
def rabbit():
    with RabbitMqContainer("rabbitmq:3.13-management") as r:
        yield r


@pytest.fixture(scope="module")
def amqp_url(rabbit):
    p = rabbit.get_exposed_port(5672)
    return f"amqp://guest:guest@127.0.0.1:{p}/"


async def test_retry_then_succeed(amqp_url):
    conn = await open_connection(amqp_url)
    queue = "test.q.retry"
    await declare_consumer_topology(conn, queue=queue, routing_keys=["order.created"])
    chan = await conn.channel()
    ex = await chan.get_exchange(MAIN_EXCHANGE, ensure=False)

    attempts: list[str] = []
    completed = asyncio.Event()
    from sqlalchemy.exc import OperationalError

    async def handler(env: Envelope) -> None:
        attempts.append(env.event_id)
        if len(attempts) < 2:
            raise OperationalError("transient", None, None)
        completed.set()

    consumer = Consumer(conn, queue_name=queue, max_retries=5)
    task = asyncio.create_task(consumer.consume(handler))

    body = json.dumps(
        {"event_id": str(uuid.uuid4()), "event_type": "order.created", "data": {}}
    ).encode()
    await ex.publish(aio_pika.Message(body=body), routing_key="order.created")

    await asyncio.wait_for(completed.wait(), timeout=10)
    assert len(attempts) >= 2
    task.cancel()
    await conn.close()


async def test_max_retries_to_dlq(amqp_url):
    conn = await open_connection(amqp_url)
    queue = "test.q.dlq"
    await declare_consumer_topology(conn, queue=queue, routing_keys=["order.created"])
    chan = await conn.channel()
    ex = await chan.get_exchange(MAIN_EXCHANGE, ensure=False)
    dlq = await chan.declare_queue(f"{queue}.dlq", durable=True)

    from sqlalchemy.exc import OperationalError

    async def handler(env: Envelope) -> None:
        raise OperationalError("nope", None, None)

    consumer = Consumer(conn, queue_name=queue, max_retries=1)
    task = asyncio.create_task(consumer.consume(handler))

    body = json.dumps(
        {"event_id": str(uuid.uuid4()), "event_type": "order.created", "data": {}}
    ).encode()
    await ex.publish(aio_pika.Message(body=body), routing_key="order.created")

    msg = None
    for _ in range(20):
        msg = await dlq.get(fail=False, timeout=2)
        if msg:
            break
        await asyncio.sleep(2)
    assert msg is not None
    task.cancel()
    await conn.close()
