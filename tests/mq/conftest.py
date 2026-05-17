from __future__ import annotations

from collections.abc import AsyncIterator

import aio_pika
import pytest
import pytest_asyncio
from testcontainers.rabbitmq import RabbitMqContainer


@pytest.fixture(scope="session")
def rabbitmq_container() -> RabbitMqContainer:
    """Shared RabbitMQ container across the test session."""
    with RabbitMqContainer("rabbitmq:3.13-management") as r:
        yield r


@pytest_asyncio.fixture
async def rabbitmq_connection(
    rabbitmq_container: RabbitMqContainer,
) -> AsyncIterator[aio_pika.abc.AbstractRobustConnection]:
    port = rabbitmq_container.get_exposed_port(5672)
    url = f"amqp://guest:guest@127.0.0.1:{port}/"
    conn = await aio_pika.connect_robust(url)
    try:
        yield conn
    finally:
        await conn.close()
