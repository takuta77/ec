from __future__ import annotations

import aio_pika


async def open_connection(url: str) -> aio_pika.abc.AbstractRobustConnection:
    return await aio_pika.connect_robust(url)
