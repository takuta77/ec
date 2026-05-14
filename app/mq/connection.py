from __future__ import annotations

import aio_pika
from fastapi import Request


async def open_connection(url: str) -> aio_pika.abc.AbstractRobustConnection:
    return await aio_pika.connect_robust(url)


def get_mq_connection(request: Request) -> aio_pika.abc.AbstractRobustConnection | None:
    """FastAPI dependency: returns the lifespan-managed RabbitMQ connection.

    Returns None if the lifespan failed to open the connection OR if the app
    was instantiated without running lifespan (e.g. some test fixtures). The
    caller is expected to handle None (typically by raising 503).
    """
    return getattr(request.app.state, "mq_connection", None)
