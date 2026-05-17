from __future__ import annotations

import asyncio
import json
import subprocess
import sys as _sys
import uuid
from pathlib import Path
from typing import Any

import aio_pika
import pytest

from app.mq.dlq_admin import (
    CountResult,
    DLQAdminError,
    DLQMessage,
    DLQNotFoundError,
    DrainResult,
    NoRoutingKeyError,
    RedriveResult,
)
from app.mq.retry import DLX_EXCHANGE, MAIN_EXCHANGE


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


@pytest.mark.slow
@pytest.mark.asyncio
async def test_peek_handles_messages_without_message_id(rabbitmq_connection) -> None:
    """Production messages don't set message_id; peek must not dedupe on it."""
    from app.mq.dlq_admin import peek_dlq

    queue = f"ec.test_peek_no_id_{uuid.uuid4().hex[:8]}"
    await _declare_dlq(rabbitmq_connection, queue)

    # Publish 3 messages with message_id=None (mimicking publisher.py).
    chan = await rabbitmq_connection.channel()
    dlx = await chan.declare_exchange(DLX_EXCHANGE, "topic", durable=True)
    for i in range(3):
        await dlx.publish(
            aio_pika.Message(
                body=f"m{i}".encode(),
                headers={"x-death-count": 1},
                # message_id intentionally omitted
            ),
            routing_key="ec.order.completed",
        )
    await chan.close()
    await asyncio.sleep(0.2)

    msgs = await peek_dlq(connection=rabbitmq_connection, queue=queue, limit=10)
    assert len(msgs) == 3, f"expected 3 but got {len(msgs)} — message_id-based dedup regression?"


@pytest.mark.slow
@pytest.mark.asyncio
async def test_redrive_dry_run_does_not_publish(rabbitmq_connection) -> None:
    from app.mq.dlq_admin import count_dlq, redrive_dlq

    queue = f"ec.test_redrive_dry_{uuid.uuid4().hex[:8]}"
    await _declare_dlq(rabbitmq_connection, queue)
    for _ in range(2):
        await _publish_to_dlq(rabbitmq_connection, routing_key="ec.order.completed", body=b"x")
    await asyncio.sleep(0.2)

    r = await redrive_dlq(connection=rabbitmq_connection, queue=queue, limit=None, dry_run=True)
    assert r.dry_run is True
    assert r.requested == 2
    assert r.redriven == 0
    assert r.skipped == []

    await asyncio.sleep(0.2)
    assert (await count_dlq(connection=rabbitmq_connection, queue=queue)).message_count == 2


@pytest.mark.slow
@pytest.mark.asyncio
async def test_redrive_apply_publishes_to_main(rabbitmq_connection) -> None:
    from app.mq.dlq_admin import count_dlq, redrive_dlq

    queue = f"ec.test_redrive_apply_{uuid.uuid4().hex[:8]}"
    routing_key = "ec.order.completed"

    # DLQ for the consumer + sink bound to MAIN to prove republish.
    chan = await rabbitmq_connection.channel()
    await chan.declare_exchange(MAIN_EXCHANGE, "topic", durable=True)
    await chan.declare_exchange(DLX_EXCHANGE, "topic", durable=True)
    dlq = await chan.declare_queue(f"{queue}.dlq", durable=True)
    await dlq.bind(DLX_EXCHANGE, routing_key="#")
    sink = await chan.declare_queue(f"{queue}.sink", durable=True)
    await sink.bind(MAIN_EXCHANGE, routing_key=routing_key)
    await chan.close()

    for i in range(2):
        await _publish_to_dlq(rabbitmq_connection, routing_key=routing_key, body=f"m{i}".encode())
    await asyncio.sleep(0.2)

    r = await redrive_dlq(connection=rabbitmq_connection, queue=queue, limit=None, dry_run=False)
    assert r.dry_run is False
    assert r.requested == 2
    assert r.redriven == 2
    assert r.skipped == []

    await asyncio.sleep(0.3)
    assert (await count_dlq(connection=rabbitmq_connection, queue=queue)).message_count == 0

    chan2 = await rabbitmq_connection.channel()
    sink_q = await chan2.declare_queue(f"{queue}.sink", passive=True)
    assert sink_q.declaration_result.message_count == 2
    await chan2.close()


@pytest.mark.slow
@pytest.mark.asyncio
async def test_redrive_skips_messages_without_routing_key(rabbitmq_connection) -> None:
    """Messages with no routing key resolvable (no x-death + no envelope routing key)
    should be skipped, not re-published."""
    from app.mq.dlq_admin import count_dlq, redrive_dlq

    queue = f"ec.test_redrive_skip_{uuid.uuid4().hex[:8]}"
    await _declare_dlq(rabbitmq_connection, queue)

    # Publish with routing key "" (empty) to break extraction; in real broker
    # behavior, AMQP routing_key is always a string (possibly empty).
    chan = await rabbitmq_connection.channel()
    dlx = await chan.declare_exchange(DLX_EXCHANGE, "topic", durable=True)
    bad_id = "no-routing-id"
    await dlx.publish(
        aio_pika.Message(body=b"orphan", headers={}, message_id=bad_id),
        routing_key="",  # empty routing key — extraction returns None
    )
    # Good message for contrast.
    await _publish_to_dlq(rabbitmq_connection, routing_key="ec.bar", body=b"good")
    await chan.close()
    await asyncio.sleep(0.2)

    r = await redrive_dlq(connection=rabbitmq_connection, queue=queue, limit=None, dry_run=False)
    # Two messages requested; one skipped (empty routing key), one redriven.
    assert r.requested == 2
    assert r.redriven == 1
    assert len(r.skipped) == 1

    # The skipped one stayed in DLQ.
    await asyncio.sleep(0.2)
    assert (await count_dlq(connection=rabbitmq_connection, queue=queue)).message_count == 1


@pytest.mark.slow
@pytest.mark.asyncio
async def test_drain_dry_run_leaves_messages(rabbitmq_connection) -> None:
    from app.mq.dlq_admin import count_dlq, drain_dlq

    queue = f"ec.test_drain_dry_{uuid.uuid4().hex[:8]}"
    await _declare_dlq(rabbitmq_connection, queue)
    for _ in range(2):
        await _publish_to_dlq(rabbitmq_connection, routing_key="ec.x", body=b"x")
    await asyncio.sleep(0.2)

    r = await drain_dlq(connection=rabbitmq_connection, queue=queue, limit=None, dry_run=True)
    assert r.dry_run is True
    assert r.drained == 0

    await asyncio.sleep(0.2)
    assert (await count_dlq(connection=rabbitmq_connection, queue=queue)).message_count == 2


@pytest.mark.slow
@pytest.mark.asyncio
async def test_drain_apply_removes_messages(rabbitmq_connection) -> None:
    from app.mq.dlq_admin import count_dlq, drain_dlq

    queue = f"ec.test_drain_apply_{uuid.uuid4().hex[:8]}"
    routing_key = "ec.x"

    chan = await rabbitmq_connection.channel()
    await chan.declare_exchange(MAIN_EXCHANGE, "topic", durable=True)
    await chan.declare_exchange(DLX_EXCHANGE, "topic", durable=True)
    dlq = await chan.declare_queue(f"{queue}.dlq", durable=True)
    await dlq.bind(DLX_EXCHANGE, routing_key="#")
    sink = await chan.declare_queue(f"{queue}.sink", durable=True)
    await sink.bind(MAIN_EXCHANGE, routing_key=routing_key)
    await chan.close()

    for _ in range(2):
        await _publish_to_dlq(rabbitmq_connection, routing_key=routing_key, body=b"x")
    await asyncio.sleep(0.2)

    r = await drain_dlq(connection=rabbitmq_connection, queue=queue, limit=None, dry_run=False)
    assert r.dry_run is False
    assert r.drained == 2

    await asyncio.sleep(0.3)
    assert (await count_dlq(connection=rabbitmq_connection, queue=queue)).message_count == 0

    chan2 = await rabbitmq_connection.channel()
    sink_q = await chan2.declare_queue(f"{queue}.sink", passive=True)
    assert sink_q.declaration_result.message_count == 0
    await chan2.close()


@pytest.mark.slow
@pytest.mark.asyncio
async def test_cli_count_smoke(rabbitmq_connection, rabbitmq_container) -> None:
    """Run `scripts/dlq.py count <queue>` against Testcontainers RabbitMQ and assert output."""
    queue = f"ec.test_cli_count_{uuid.uuid4().hex[:8]}"
    await _declare_dlq(rabbitmq_connection, queue)
    await _publish_to_dlq(rabbitmq_connection, routing_key="ec.x", body=b"x")
    await asyncio.sleep(0.2)

    port = rabbitmq_container.get_exposed_port(5672)
    url = f"amqp://guest:guest@127.0.0.1:{port}/"

    env = {
        "RABBITMQ_URL": url,
        "DATABASE_URL": "postgresql+asyncpg://x:x@localhost/x",
        "JWT_PRIVATE_KEY_PATH": "/tmp/x.pem",
        "JWT_PUBLIC_KEY_PATH": "/tmp/x.pem",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317",
        "PATH": "/usr/bin:/bin",
    }
    # Repo root = two levels up from tests/mq/test_dlq_admin.py — portable across
    # local worktrees and CI runners.
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [_sys.executable, "scripts/dlq.py", "count", queue],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        cwd=repo_root,
    )
    assert result.returncode == 0, f"stderr={result.stderr}\nstdout={result.stdout}"
    assert f"{queue}.dlq: 1" in result.stdout
