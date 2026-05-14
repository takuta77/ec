"""DLQ admin helpers — reusable from CLI / HTTP / cron / monitoring jobs.

The public API is:
    count_dlq, peek_dlq, redrive_dlq, drain_dlq

Each takes an `aio_pika.AbstractRobustConnection` and returns a typed
dataclass. Mutating operations (redrive / drain) default to `dry_run=True`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import aio_pika
import structlog


_logger = structlog.get_logger("ec.mq.dlq_admin")


class DLQAdminError(Exception):
    """Base for DLQ admin operations."""


class DLQNotFoundError(DLQAdminError):
    """Queue does not exist (passive declare failed)."""


class NoRoutingKeyError(DLQAdminError):
    """Cannot determine original routing key for redrive (no x-death header)."""


@dataclass(frozen=True, slots=True)
class CountResult:
    queue: str
    message_count: int


@dataclass(frozen=True, slots=True)
class DLQMessage:
    delivery_tag: int
    event_id: str | None
    routing_key: str | None
    death_count: int
    body_preview: str
    headers: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RedriveResult:
    queue: str
    dry_run: bool
    requested: int
    redriven: int
    skipped: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DrainResult:
    queue: str
    dry_run: bool
    drained: int


async def count_dlq(
    *,
    connection: aio_pika.abc.AbstractRobustConnection,
    queue: str,
) -> CountResult:
    """Return the message count of `<queue>.dlq` via passive declare."""
    dlq_name = f"{queue}.dlq"
    chan = await connection.channel()
    try:
        try:
            declared = await chan.declare_queue(dlq_name, passive=True)
        except aio_pika.exceptions.ChannelClosed as exc:
            raise DLQNotFoundError(f"queue {dlq_name} does not exist") from exc
        return CountResult(
            queue=dlq_name,
            message_count=declared.declaration_result.message_count or 0,
        )
    finally:
        if not chan.is_closed:
            await chan.close()


async def peek_dlq(
    *,
    connection: aio_pika.abc.AbstractRobustConnection,
    queue: str,
    limit: int,
    preview_chars: int = 200,
) -> list[DLQMessage]:
    """Return up to `limit` messages without consuming them."""
    raise NotImplementedError


async def redrive_dlq(
    *,
    connection: aio_pika.abc.AbstractRobustConnection,
    queue: str,
    limit: int | None,
    dry_run: bool = True,
) -> RedriveResult:
    """Re-publish DLQ messages to the main exchange using their original routing key."""
    raise NotImplementedError


async def drain_dlq(
    *,
    connection: aio_pika.abc.AbstractRobustConnection,
    queue: str,
    limit: int | None,
    dry_run: bool = True,
) -> DrainResult:
    """Permanently discard up to `limit` messages from `<queue>.dlq`."""
    raise NotImplementedError
