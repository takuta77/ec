import json
import pytest
from testcontainers.rabbitmq import RabbitMqContainer

from app.mq.connection import open_connection
from app.mq.publisher import Publisher


pytestmark = [pytest.mark.slow, pytest.mark.asyncio]


@pytest.fixture(scope="module")
def rabbit():
    with RabbitMqContainer("rabbitmq:3.13-management") as r:
        yield r


@pytest.fixture(scope="module")
def amqp_url(rabbit):
    p = rabbit.get_exposed_port(5672)
    return f"amqp://guest:guest@127.0.0.1:{p}/"


async def test_publish_message_arrives(amqp_url):
    conn = await open_connection(amqp_url)
    pub = Publisher(conn, exchange="ec.events")
    await pub.declare()

    chan = await conn.channel()
    queue = await chan.declare_queue("test-q", durable=False, auto_delete=True)
    await queue.bind("ec.events", routing_key="checkout.requested")

    await pub.publish(routing_key="checkout.requested", body=json.dumps({"k": 1}).encode(), headers={"traceparent": "tp"})

    msg = await queue.get(timeout=5)
    assert msg is not None
    assert json.loads(msg.body) == {"k": 1}
    assert msg.headers.get("traceparent") == "tp"

    await conn.close()
