from __future__ import annotations

from typing import Any

import aio_pika


class Publisher:
    def __init__(self, connection: aio_pika.abc.AbstractRobustConnection, *, exchange: str) -> None:
        self.connection = connection
        self.exchange_name = exchange
        self._exchange: aio_pika.abc.AbstractExchange | None = None

    async def declare(self) -> None:
        channel = await self.connection.channel(publisher_confirms=True)
        self._exchange = await channel.declare_exchange(
            self.exchange_name, aio_pika.ExchangeType.TOPIC, durable=True
        )

    async def publish(self, *, routing_key: str, body: bytes, headers: dict[str, Any]) -> None:
        if self._exchange is None:
            await self.declare()
        assert self._exchange is not None
        await self._exchange.publish(
            aio_pika.Message(
                body=body, headers=headers, delivery_mode=aio_pika.DeliveryMode.PERSISTENT
            ),
            routing_key=routing_key,
        )
