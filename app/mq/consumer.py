from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import aio_pika
from sqlalchemy.exc import DBAPIError, DisconnectionError, OperationalError

from app.mq.retry import RETRY_BACKOFF_MS, RETRY_EXCHANGE

logger = logging.getLogger(__name__)

RETRYABLE_EXCEPTIONS = (OperationalError, DBAPIError, DisconnectionError, ConnectionError, TimeoutError, OSError)


@dataclass
class Envelope:
    event_id: str
    event_type: str
    data: dict[str, Any]
    raw_headers: dict[str, Any]

    @classmethod
    def parse(cls, body: bytes, headers: dict[str, Any]) -> "Envelope":
        payload = json.loads(body)
        return cls(
            event_id=payload["event_id"],
            event_type=payload["event_type"],
            data=payload["data"],
            raw_headers=headers or {},
        )


HandlerFn = Callable[[Envelope], Awaitable[None]]


class Consumer:
    def __init__(
        self,
        connection: aio_pika.abc.AbstractRobustConnection,
        *,
        queue_name: str,
        max_retries: int,
        on_dlq: Callable[[Envelope, str], Awaitable[None]] | None = None,
    ) -> None:
        self.connection = connection
        self.queue_name = queue_name
        self.max_retries = max_retries
        self.on_dlq = on_dlq

    def _attempt_count(self, headers: dict[str, Any]) -> int:
        x_death = headers.get("x-death") or []
        if x_death:
            return int(x_death[0].get("count", 0))
        return 0

    async def consume(self, handler: HandlerFn) -> None:
        chan = await self.connection.channel()
        await chan.set_qos(prefetch_count=16)
        queue = await chan.get_queue(self.queue_name, ensure=False)
        retry_ex = await chan.get_exchange(RETRY_EXCHANGE, ensure=False)

        async with queue.iterator() as it:
            async for message in it:
                async with message.process(ignore_processed=True, requeue=False, reject_on_redelivered=False):
                    try:
                        envelope = Envelope.parse(message.body, dict(message.headers or {}))
                    except Exception as exc:  # parse / schema error
                        logger.warning("non-retryable parse error: %s", exc)
                        await message.reject(requeue=False)
                        if self.on_dlq:
                            await self.on_dlq(
                                Envelope(event_id="-", event_type="-", data={}, raw_headers=dict(message.headers or {})),
                                "parse_error",
                            )
                        continue

                    attempt = self._attempt_count(envelope.raw_headers)
                    try:
                        await handler(envelope)
                    except RETRYABLE_EXCEPTIONS as exc:
                        if attempt >= self.max_retries:
                            logger.warning("DLQ after max retries: %s", exc)
                            await message.reject(requeue=False)
                            if self.on_dlq:
                                await self.on_dlq(envelope, "max_retries")
                            continue
                        ttl = RETRY_BACKOFF_MS[min(attempt, len(RETRY_BACKOFF_MS) - 1)]
                        await retry_ex.publish(
                            aio_pika.Message(
                                body=message.body,
                                headers=envelope.raw_headers,
                                expiration=ttl / 1000.0,
                                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                            ),
                            routing_key=envelope.event_type,
                        )
                        await message.ack()
                    except Exception:  # non-retryable
                        logger.exception("non-retryable error")
                        await message.reject(requeue=False)
                        if self.on_dlq:
                            await self.on_dlq(envelope, "non_retryable")
                    else:
                        await message.ack()
