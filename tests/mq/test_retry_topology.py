import pytest
from testcontainers.rabbitmq import RabbitMqContainer

from app.mq.connection import open_connection
from app.mq.retry import declare_consumer_topology


pytestmark = [pytest.mark.slow, pytest.mark.asyncio]


@pytest.fixture(scope="module")
def rabbit():
    with RabbitMqContainer("rabbitmq:3.13-management") as r:
        yield r


@pytest.fixture(scope="module")
def amqp_url(rabbit):
    p = rabbit.get_exposed_port(5672)
    return f"amqp://guest:guest@127.0.0.1:{p}/"


async def test_declare_topology_idempotent(amqp_url):
    conn = await open_connection(amqp_url)
    await declare_consumer_topology(
        conn, queue="ec.api.order-events", routing_keys=["order.created", "order.failed"]
    )
    # Idempotent on second call
    await declare_consumer_topology(
        conn, queue="ec.api.order-events", routing_keys=["order.created", "order.failed"]
    )
    await conn.close()
