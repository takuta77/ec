from __future__ import annotations

from collections.abc import Iterable

import aio_pika

MAIN_EXCHANGE = "ec.events"
RETRY_EXCHANGE = "ec.events.retry"
DLX_EXCHANGE = "ec.events.dlx"

RETRY_BACKOFF_MS = [1000, 5000, 30000, 120000, 600000]


async def declare_consumer_topology(
    connection: aio_pika.abc.AbstractRobustConnection,
    *,
    queue: str,
    routing_keys: Iterable[str],
) -> aio_pika.abc.AbstractQueue:
    chan = await connection.channel()
    main_ex = await chan.declare_exchange(MAIN_EXCHANGE, "topic", durable=True)
    retry_ex = await chan.declare_exchange(RETRY_EXCHANGE, "topic", durable=True)
    dlx_ex = await chan.declare_exchange(DLX_EXCHANGE, "topic", durable=True)

    main_q = await chan.declare_queue(
        queue,
        durable=True,
        arguments={"x-dead-letter-exchange": DLX_EXCHANGE},
    )
    for rk in routing_keys:
        await main_q.bind(main_ex, routing_key=rk)

    for ttl in RETRY_BACKOFF_MS:
        retry_q = await chan.declare_queue(
            f"{queue}.retry.{ttl}",
            durable=True,
            arguments={
                "x-message-ttl": ttl,
                "x-dead-letter-exchange": MAIN_EXCHANGE,
            },
        )
        for rk in routing_keys:
            await retry_q.bind(retry_ex, routing_key=rk)

    dlq = await chan.declare_queue(f"{queue}.dlq", durable=True)
    for rk in routing_keys:
        await dlq.bind(dlx_ex, routing_key=rk)

    return main_q
