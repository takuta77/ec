from __future__ import annotations

import aio_pika
from fastapi import Request


async def open_connection(url: str) -> aio_pika.abc.AbstractRobustConnection:
    return await aio_pika.connect_robust(url)


def get_mq_connection(request: Request) -> aio_pika.abc.AbstractRobustConnection | None:
    """FastAPI dependency: returns the lifespan-managed RabbitMQ connection (may be None)."""
    return request.app.state.mq_connection  # type: ignore[no-any-return]
