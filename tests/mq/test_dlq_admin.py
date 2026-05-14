from __future__ import annotations

import asyncio
import uuid
from typing import Any

import aio_pika
import pytest

import json

from app.mq.dlq_admin import (
    CountResult,
    DLQAdminError,
    DLQMessage,
    DLQNotFoundError,
    DrainResult,
    NoRoutingKeyError,
    RedriveResult,
)
from app.mq.retry import DLX_EXCHANGE


def test_exception_hierarchy() -> None:
    assert issubclass(DLQNotFoundError, DLQAdminError)
    assert issubclass(NoRoutingKeyError, DLQAdminError)


def test_count_result_dataclass() -> None:
    r = CountResult(queue="ec.foo.dlq", message_count=3)
    assert r.queue == "ec.foo.dlq"
    assert r.message_count == 3


def test_dlq_message_dataclass() -> None:
    m = DLQMessage(
        delivery_tag=1,
        event_id="evt-1",
        routing_key="ec.order.completed",
        death_count=5,
        body_preview="abc",
        headers={"x-death": []},
    )
    assert m.event_id == "evt-1"
    assert m.death_count == 5


def test_redrive_result_dataclass() -> None:
    r = RedriveResult(queue="ec.foo.dlq", dry_run=True, requested=2, redriven=0, skipped=[])
    assert r.dry_run is True
    assert r.redriven == 0


def test_drain_result_dataclass() -> None:
    r = DrainResult(queue="ec.foo.dlq", dry_run=False, drained=2)
    assert r.drained == 2


@pytest.mark.asyncio
async def test_stubs_raise_not_implemented() -> None:
    from app.mq.dlq_admin import drain_dlq, redrive_dlq

    with pytest.raises(NotImplementedError):
        await redrive_dlq(connection=None, queue="x", limit=1)  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError):
        await drain_dlq(connection=None, queue="x", limit=1)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Slow integration tests (require Docker / Testcontainers)
# ---------------------------------------------------------------------------


async def _declare_dlq(connection: aio_pika.abc.AbstractRobustConnection, queue: str) -> None:
    """Set up `<queue>.dlq` bound to DLX exchange, mirroring `declare_consumer_topology`."""
    chan = await connection.channel()
    await chan.declare_exchange(DLX_EXCHANGE, "topic", durable=True)
    dlq = await chan.declare_queue(f"{queue}.dlq", durable=True)
    await dlq.bind(DLX_EXCHANGE, routing_key="#")
    await chan.close()


async def _publish_to_dlq(
    connection: aio_pika.abc.AbstractRobustConnection,
    *,
    routing_key: str,
    body: bytes,
    extra_headers: dict[str, Any] | None = None,
) -> None:
    chan = await connection.channel()
    dlx = await chan.declare_exchange(DLX_EXCHANGE, "topic", durable=True)
    headers: dict[str, Any] = {
        "x-death": [{"routing-keys": [routing_key], "count": 3}],
        "x-death-count": 3,
    }
    if extra_headers:
        headers.update(extra_headers)
    await dlx.publish(
        aio_pika.Message(body=body, headers=headers, message_id=str(uuid.uuid4())),
        routing_key=routing_key,
    )
    await chan.close()


@pytest.mark.slow
@pytest.mark.asyncio
async def test_count_empty(rabbitmq_connection) -> None:
    from app.mq.dlq_admin import count_dlq

    queue = f"ec.test_count_empty_{uuid.uuid4().hex[:8]}"
    await _declare_dlq(rabbitmq_connection, queue)

    r = await count_dlq(connection=rabbitmq_connection, queue=queue)
    assert r.queue == f"{queue}.dlq"
    assert r.message_count == 0


@pytest.mark.slow
@pytest.mark.asyncio
async def test_count_with_messages(rabbitmq_connection) -> None:
    from app.mq.dlq_admin import count_dlq

    queue = f"ec.test_count_msgs_{uuid.uuid4().hex[:8]}"
    await _declare_dlq(rabbitmq_connection, queue)
    for i in range(3):
        await _publish_to_dlq(
            rabbitmq_connection, routing_key="ec.order.completed", body=f"m{i}".encode()
        )
    await asyncio.sleep(0.2)

    r = await count_dlq(connection=rabbitmq_connection, queue=queue)
    assert r.message_count == 3


@pytest.mark.slow
@pytest.mark.asyncio
async def test_count_queue_not_found(rabbitmq_connection) -> None:
    from app.mq.dlq_admin import DLQNotFoundError, count_dlq

    with pytest.raises(DLQNotFoundError):
        await count_dlq(
            connection=rabbitmq_connection, queue=f"ec.does_not_exist_{uuid.uuid4().hex[:8]}"
        )


@pytest.mark.slow
@pytest.mark.asyncio
async def test_peek_returns_messages_without_consuming(rabbitmq_connection) -> None:
    from app.mq.dlq_admin import count_dlq, peek_dlq

    queue = f"ec.test_peek_{uuid.uuid4().hex[:8]}"
    await _declare_dlq(rabbitmq_connection, queue)
    for i in range(3):
        body = json.dumps({"i": i}).encode()
        await _publish_to_dlq(rabbitmq_connection, routing_key="ec.order.completed", body=body)
    await asyncio.sleep(0.2)

    msgs = await peek_dlq(connection=rabbitmq_connection, queue=queue, limit=10)
    assert len(msgs) == 3
    assert all(m.routing_key == "ec.order.completed" for m in msgs)
    assert all(m.death_count == 3 for m in msgs)
    assert any('"i":' in m.body_preview for m in msgs)

    # Non-destructive: count still 3.
    await asyncio.sleep(0.2)
    r = await count_dlq(connection=rabbitmq_connection, queue=queue)
    assert r.message_count == 3


@pytest.mark.slow
@pytest.mark.asyncio
async def test_peek_respects_limit(rabbitmq_connection) -> None:
    from app.mq.dlq_admin import peek_dlq

    queue = f"ec.test_peek_limit_{uuid.uuid4().hex[:8]}"
    await _declare_dlq(rabbitmq_connection, queue)
    for i in range(5):
        await _publish_to_dlq(rabbitmq_connection, routing_key="ec.x", body=f"m{i}".encode())
    await asyncio.sleep(0.2)

    msgs = await peek_dlq(connection=rabbitmq_connection, queue=queue, limit=2)
    assert len(msgs) == 2


def test_peek_preview_truncates_long_body() -> None:
    from app.mq.dlq_admin import _body_preview

    long_body = b"a" * 500
    assert _body_preview(long_body, 200) == "a" * 200
    assert _body_preview(b"\xff\xff", 200).startswith("b'")
