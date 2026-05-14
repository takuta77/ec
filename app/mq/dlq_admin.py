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


def _body_preview(body: bytes, preview_chars: int) -> str:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return repr(body)[:preview_chars]
    return text[:preview_chars]


def _extract_routing_key(headers: dict[str, Any]) -> str | None:
    x_death = headers.get("x-death")
    if not isinstance(x_death, list) or not x_death:
        return None
    first = x_death[0]
    if not isinstance(first, dict):
        return None
    rks = first.get("routing-keys")
    if not isinstance(rks, list) or not rks:
        return None
    first_rk = rks[0]
    return first_rk if isinstance(first_rk, str) else None


def _extract_death_count(headers: dict[str, Any]) -> int:
    x_death = headers.get("x-death")
    if isinstance(x_death, list) and x_death:
        first = x_death[0]
        if isinstance(first, dict):
            count = first.get("count", 0)
            if isinstance(count, int):
                return count
    # Fallback: flat header set by test helpers or custom publishers
    flat = headers.get("x-death-count", 0)
    return flat if isinstance(flat, int) else 0


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
    dlq_name = f"{queue}.dlq"
    chan = await connection.channel()
    await chan.set_qos(prefetch_count=limit)
    try:
        try:
            dlq = await chan.declare_queue(dlq_name, passive=True)
        except aio_pika.exceptions.ChannelClosed as exc:
            raise DLQNotFoundError(f"queue {dlq_name} does not exist") from exc

        result: list[DLQMessage] = []
        pending: list[aio_pika.abc.AbstractIncomingMessage] = []
        seen_tags: set[int] = set()

        async with dlq.iterator(no_ack=False, timeout=2.0) as q_iter:
            try:
                async for message in q_iter:
                    # Stop on re-delivery of an already-seen delivery_tag to avoid cycles.
                    # (message_id is not reliable: production publisher.py never sets it.)
                    # delivery_tag is typed int | None by aio_pika but is always an int in
                    # practice; guard for type-checker correctness.
                    tag = message.delivery_tag
                    if tag is None or tag in seen_tags:
                        await message.nack(requeue=True)
                        break
                    seen_tags.add(tag)
                    headers = dict(message.headers or {})
                    rk = _extract_routing_key(headers) or message.routing_key
                    result.append(
                        DLQMessage(
                            delivery_tag=message.delivery_tag or 0,
                            event_id=message.message_id,
                            routing_key=rk,
                            death_count=_extract_death_count(headers),
                            body_preview=_body_preview(message.body, preview_chars),
                            headers=headers,
                        )
                    )
                    pending.append(message)
                    if len(result) >= limit:
                        break
            except TimeoutError:
                pass

        # Nack all collected messages after iterator is closed (outside consumer scope)
        for msg in pending:
            try:
                await msg.nack(requeue=True)
            except Exception:  # noqa: BLE001
                pass

        _logger.info("dlq_admin.peek", queue=dlq_name, count=len(result))
        return result
    finally:
        if not chan.is_closed:
            await chan.close()


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
