# DLQ Admin Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement reusable DLQ admin helpers (`count`/`peek`/`redrive`/`drain`) in `app/mq/dlq_admin.py` and a thin CLI wrapper `scripts/dlq.py`, all backed by Testcontainers-based integration tests.

**Architecture:** Strict logic-vs-presentation split. Logic layer (`app/mq/dlq_admin.py`) takes an `aio_pika.AbstractRobustConnection`, returns typed dataclasses, raises typed exceptions, and emits structlog. CLI layer (`scripts/dlq.py`) handles argparse + result formatting + exit codes only. Same helpers will be the foundation for a future HTTP admin endpoint (C-4) and scheduled monitoring jobs.

**Tech Stack:** Python 3.12 + uv, `aio-pika`, `structlog`, `pytest-asyncio`, `testcontainers[rabbitmq]`, argparse (stdlib).

---

## Working Branch

Working directory: `/Users/takuma/cross/ec/.worktrees/dlq-admin-tools`
Branch: `feature/dlq-admin-tools` (off `origin/main` at `9b86f99`).
Spec: `docs/superpowers/specs/2026-05-15-dlq-admin-tools-design.md`.

---

## File Structure

```
app/mq/
└── dlq_admin.py                       # new — exceptions, dataclasses, 4 helpers

scripts/
└── dlq.py                             # new — CLI entrypoint (argparse + formatter)

tests/mq/
└── test_dlq_admin.py                  # new — unit + slow tests
```

The existing `app/mq/retry.py` provides `MAIN_EXCHANGE`, `DLX_EXCHANGE` constants. Reuse them.

---

## Task 1: Skeleton — exceptions, dataclasses, module file

**Files:**
- Create: `app/mq/dlq_admin.py`
- Create: `tests/mq/test_dlq_admin.py` (with one initial unit test)

**Goal:** Establish the public type surface. The 4 functions exist as stubs that raise `NotImplementedError`. Tests confirm imports work and dataclasses are constructible.

- [ ] **Step 1: Verify existing tests directory and constants we'll reuse**

```bash
ls tests/mq/
grep -n "MAIN_EXCHANGE\|DLX_EXCHANGE" app/mq/retry.py
```

Expected: `MAIN_EXCHANGE = "ec.events"`, `DLX_EXCHANGE = "ec.events.dlx"` shown.

- [ ] **Step 2: Write the failing unit tests**

`tests/mq/test_dlq_admin.py`:

```python
from __future__ import annotations

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
    # Until Tasks 2-5, the public functions exist but raise NotImplementedError.
    from app.mq.dlq_admin import count_dlq, drain_dlq, peek_dlq, redrive_dlq

    with pytest.raises(NotImplementedError):
        await count_dlq(connection=None, queue="x")  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError):
        await peek_dlq(connection=None, queue="x", limit=1)  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError):
        await redrive_dlq(connection=None, queue="x", limit=1)  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError):
        await drain_dlq(connection=None, queue="x", limit=1)  # type: ignore[arg-type]
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run pytest tests/mq/test_dlq_admin.py -v
```

Expected: ImportError because `app.mq.dlq_admin` doesn't exist yet.

- [ ] **Step 4: Create the module skeleton**

`app/mq/dlq_admin.py`:

```python
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
    raise NotImplementedError


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
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/mq/test_dlq_admin.py -v
```

Expected: 6 PASS.

- [ ] **Step 6: Run lint + type**

```bash
uv run ruff check app/mq/dlq_admin.py tests/mq/test_dlq_admin.py
uv run ruff format --check app/mq/dlq_admin.py tests/mq/test_dlq_admin.py
uv run mypy app/mq/dlq_admin.py
```

Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add app/mq/dlq_admin.py tests/mq/test_dlq_admin.py
git commit -m "feat(mq): dlq_admin skeleton — exceptions, dataclasses, stub helpers"
```

---

## Task 2: Implement `count_dlq` (passive declare)

**Files:**
- Modify: `app/mq/dlq_admin.py`
- Modify: `tests/mq/test_dlq_admin.py` (append slow tests)

- [ ] **Step 1: Inspect existing slow test fixtures**

```bash
grep -n "rabbitmq\|RabbitMq\|aio_pika" tests/conftest.py | head -20
ls tests/mq/
```

Expected to see existing RabbitMQ Testcontainer fixture or pattern in `tests/mq/test_retry_topology.py`.

```bash
cat tests/mq/test_retry_topology.py
```

(Use the existing fixture pattern verbatim.)

- [ ] **Step 2: Append a slow test fixture + first count tests to `tests/mq/test_dlq_admin.py`**

After the existing tests, append:

```python
import asyncio
import json
import uuid

import structlog

from app.mq.retry import DLX_EXCHANGE, MAIN_EXCHANGE


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
    }
    if extra_headers:
        headers.update(extra_headers)
    await dlx.publish(
        aio_pika.Message(body=body, headers=headers, message_id=str(uuid.uuid4())),
        routing_key=routing_key,
    )
    await chan.close()


pytestmark_slow = [pytest.mark.slow, pytest.mark.asyncio]


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
        await _publish_to_dlq(rabbitmq_connection, routing_key="ec.order.completed", body=f"m{i}".encode())
    # Give RabbitMQ a moment to route via DLX exchange.
    await asyncio.sleep(0.2)

    r = await count_dlq(connection=rabbitmq_connection, queue=queue)
    assert r.message_count == 3


@pytest.mark.slow
@pytest.mark.asyncio
async def test_count_queue_not_found(rabbitmq_connection) -> None:
    from app.mq.dlq_admin import DLQNotFoundError, count_dlq

    with pytest.raises(DLQNotFoundError):
        await count_dlq(connection=rabbitmq_connection, queue=f"ec.does_not_exist_{uuid.uuid4().hex[:8]}")
```

Note: `rabbitmq_connection` fixture: if `tests/conftest.py` doesn't already provide one, search `tests/mq/test_retry_topology.py` to find the existing pattern (likely `rabbitmq_container` + a derived `rabbitmq_connection`) and reuse it. If neither exists, add a session-scoped fixture in `tests/mq/conftest.py`:

```python
# tests/mq/conftest.py  (create only if there is no existing equivalent)
from __future__ import annotations

from collections.abc import AsyncIterator

import aio_pika
import pytest_asyncio
from testcontainers.rabbitmq import RabbitMqContainer


@pytest_asyncio.fixture(scope="session")
async def rabbitmq_container() -> AsyncIterator[RabbitMqContainer]:
    with RabbitMqContainer("rabbitmq:3.13-management") as rmq:
        yield rmq


@pytest_asyncio.fixture
async def rabbitmq_connection(rabbitmq_container) -> AsyncIterator[aio_pika.abc.AbstractRobustConnection]:
    params = rabbitmq_container.get_connection_params()
    url = f"amqp://{params.credentials.username}:{params.credentials.password}@{params.host}:{params.port}/"
    conn = await aio_pika.connect_robust(url)
    try:
        yield conn
    finally:
        await conn.close()
```

**Always check for existing fixture first** before adding this file.

- [ ] **Step 3: Run the slow tests, confirm they FAIL on NotImplementedError**

```bash
uv run pytest tests/mq/test_dlq_admin.py -v -m slow
```

Expected: 3 FAIL with `NotImplementedError`.

- [ ] **Step 4: Implement `count_dlq`**

In `app/mq/dlq_admin.py`, replace the `count_dlq` body:

```python
async def count_dlq(
    *,
    connection: aio_pika.abc.AbstractRobustConnection,
    queue: str,
) -> CountResult:
    dlq_name = f"{queue}.dlq"
    chan = await connection.channel()
    try:
        try:
            declared = await chan.declare_queue(dlq_name, passive=True)
        except aio_pika.exceptions.ChannelClosed as exc:
            raise DLQNotFoundError(f"queue {dlq_name} does not exist") from exc
        return CountResult(queue=dlq_name, message_count=declared.declaration_result.message_count)
    finally:
        if not chan.is_closed:
            await chan.close()
```

Also add `import structlog` at top of file and:

```python
_logger = structlog.get_logger("ec.mq.dlq_admin")
```

(We'll use it for structured logging from Tasks 3-5; introducing it here keeps later edits small.)

- [ ] **Step 5: Run the slow tests, confirm 3 PASS + 6 unit tests still pass**

```bash
uv run pytest tests/mq/test_dlq_admin.py -v
```

Expected: 9 PASS total.

- [ ] **Step 6: Lint + type + format**

```bash
uv run ruff check app/mq/dlq_admin.py tests/mq/test_dlq_admin.py
uv run ruff format --check app/mq/dlq_admin.py tests/mq/test_dlq_admin.py
uv run mypy app/mq/dlq_admin.py
```

- [ ] **Step 7: Commit**

```bash
git add app/mq/dlq_admin.py tests/mq/test_dlq_admin.py tests/mq/conftest.py 2>/dev/null
# Some files may not exist; that's fine — git add ignores them silently when path expanded.
git commit -m "feat(mq): count_dlq with passive declare + slow tests"
```

(If `tests/mq/conftest.py` wasn't created — because an existing fixture covered it — that's fine; the `git add` will simply not stage a nonexistent file.)

---

## Task 3: Implement `peek_dlq` (non-destructive)

**Files:**
- Modify: `app/mq/dlq_admin.py`
- Modify: `tests/mq/test_dlq_admin.py` (append peek slow tests)

- [ ] **Step 1: Append peek slow tests**

Append to `tests/mq/test_dlq_admin.py`:

```python
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
    # All messages must include the routing_key extracted from x-death.
    assert all(m.routing_key == "ec.order.completed" for m in msgs)
    # And the death_count from x-death[0].count.
    assert all(m.death_count == 3 for m in msgs)
    # body_preview should contain the JSON we encoded.
    assert any('"i":' in m.body_preview for m in msgs)

    # Non-destructive: count is still 3.
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


@pytest.mark.asyncio
async def test_peek_preview_truncates_long_body() -> None:
    # Unit test for the preview-truncation helper.
    from app.mq.dlq_admin import _body_preview  # private helper

    long = b"a" * 500
    assert _body_preview(long, 200) == "a" * 200
    # Non-UTF-8 falls back to repr.
    assert _body_preview(b"\xff\xff", 200).startswith("b'")
```

- [ ] **Step 2: Run tests, confirm peek tests fail with NotImplementedError**

```bash
uv run pytest tests/mq/test_dlq_admin.py -v
```

Expected: count tests (3) + dataclass/exception unit tests (6) PASS, peek tests (3) FAIL.

- [ ] **Step 3: Implement `peek_dlq` and `_body_preview` helper**

In `app/mq/dlq_admin.py`, replace `peek_dlq` and add the helper:

```python
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
    if not isinstance(x_death, list) or not x_death:
        return 0
    first = x_death[0]
    if not isinstance(first, dict):
        return 0
    count = first.get("count", 0)
    return count if isinstance(count, int) else 0


async def peek_dlq(
    *,
    connection: aio_pika.abc.AbstractRobustConnection,
    queue: str,
    limit: int,
    preview_chars: int = 200,
) -> list[DLQMessage]:
    dlq_name = f"{queue}.dlq"
    chan = await connection.channel()
    await chan.set_qos(prefetch_count=limit)
    try:
        try:
            dlq = await chan.declare_queue(dlq_name, passive=True)
        except aio_pika.exceptions.ChannelClosed as exc:
            raise DLQNotFoundError(f"queue {dlq_name} does not exist") from exc

        result: list[DLQMessage] = []
        async with dlq.iterator(no_ack=False, timeout=2.0) as q_iter:
            try:
                async for message in q_iter:
                    headers = dict(message.headers or {})
                    result.append(
                        DLQMessage(
                            delivery_tag=message.delivery_tag,
                            event_id=message.message_id,
                            routing_key=_extract_routing_key(headers),
                            death_count=_extract_death_count(headers),
                            body_preview=_body_preview(message.body, preview_chars),
                            headers=headers,
                        )
                    )
                    await message.nack(requeue=True)
                    if len(result) >= limit:
                        break
            except TimeoutError:
                pass  # No more messages available within the iterator timeout.
        _logger.info("dlq_admin.peek", queue=dlq_name, count=len(result))
        return result
    finally:
        if not chan.is_closed:
            await chan.close()
```

- [ ] **Step 4: Run tests, confirm all pass**

```bash
uv run pytest tests/mq/test_dlq_admin.py -v
```

Expected: 12 PASS (6 unit + 3 count + 3 peek).

- [ ] **Step 5: Lint + type + format**

```bash
uv run ruff check app/mq/dlq_admin.py tests/mq/test_dlq_admin.py
uv run ruff format --check app/mq/dlq_admin.py tests/mq/test_dlq_admin.py
uv run mypy app/mq/dlq_admin.py
```

- [ ] **Step 6: Commit**

```bash
git add app/mq/dlq_admin.py tests/mq/test_dlq_admin.py
git commit -m "feat(mq): peek_dlq non-destructive read + preview/header helpers"
```

---

## Task 4: Implement `redrive_dlq` (dry-run by default)

**Files:**
- Modify: `app/mq/dlq_admin.py`
- Modify: `tests/mq/test_dlq_admin.py`

- [ ] **Step 1: Append redrive slow tests**

```python
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

    # DLQ still has 2 messages.
    await asyncio.sleep(0.2)
    assert (await count_dlq(connection=rabbitmq_connection, queue=queue)).message_count == 2


@pytest.mark.slow
@pytest.mark.asyncio
async def test_redrive_apply_publishes_to_main(rabbitmq_connection) -> None:
    from app.mq.dlq_admin import count_dlq, redrive_dlq

    queue = f"ec.test_redrive_apply_{uuid.uuid4().hex[:8]}"
    routing_key = "ec.order.completed"

    # Set up: DLQ for the consumer queue, plus a separate test-only queue bound to the
    # MAIN exchange so we can prove the redriven messages got re-published.
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

    # Confirm sink received both messages.
    chan2 = await rabbitmq_connection.channel()
    sink_q = await chan2.declare_queue(f"{queue}.sink", passive=True)
    assert sink_q.declaration_result.message_count == 2
    await chan2.close()


@pytest.mark.slow
@pytest.mark.asyncio
async def test_redrive_skips_messages_without_routing_key(rabbitmq_connection) -> None:
    from app.mq.dlq_admin import count_dlq, redrive_dlq

    queue = f"ec.test_redrive_skip_{uuid.uuid4().hex[:8]}"
    await _declare_dlq(rabbitmq_connection, queue)

    # Publish a message with NO x-death header — simulating a manually injected msg.
    chan = await rabbitmq_connection.channel()
    dlx = await chan.declare_exchange(DLX_EXCHANGE, "topic", durable=True)
    bad_id = "no-routing-id"
    await dlx.publish(
        aio_pika.Message(body=b"orphan", headers={}, message_id=bad_id),
        routing_key="ec.foo",
    )
    # And a normal one for contrast.
    await _publish_to_dlq(rabbitmq_connection, routing_key="ec.bar", body=b"good")
    await chan.close()
    await asyncio.sleep(0.2)

    r = await redrive_dlq(connection=rabbitmq_connection, queue=queue, limit=None, dry_run=False)
    assert r.requested == 2
    assert r.redriven == 1
    assert r.skipped == [bad_id]

    # The bad message stays in the DLQ; the good one was redriven.
    await asyncio.sleep(0.2)
    assert (await count_dlq(connection=rabbitmq_connection, queue=queue)).message_count == 1
```

- [ ] **Step 2: Run tests, confirm 3 new redrive tests fail with NotImplementedError**

```bash
uv run pytest tests/mq/test_dlq_admin.py -v
```

- [ ] **Step 3: Implement `redrive_dlq`**

Replace `redrive_dlq` body in `app/mq/dlq_admin.py`:

```python
async def redrive_dlq(
    *,
    connection: aio_pika.abc.AbstractRobustConnection,
    queue: str,
    limit: int | None,
    dry_run: bool = True,
) -> RedriveResult:
    dlq_name = f"{queue}.dlq"
    chan = await connection.channel()
    if limit is not None:
        await chan.set_qos(prefetch_count=limit)
    try:
        try:
            dlq = await chan.declare_queue(dlq_name, passive=True)
        except aio_pika.exceptions.ChannelClosed as exc:
            raise DLQNotFoundError(f"queue {dlq_name} does not exist") from exc

        main_ex = await chan.declare_exchange(MAIN_EXCHANGE, "topic", durable=True, passive=False)

        requested = 0
        redriven = 0
        skipped: list[str] = []
        async with dlq.iterator(no_ack=False, timeout=2.0) as q_iter:
            try:
                async for message in q_iter:
                    requested += 1
                    headers = dict(message.headers or {})
                    rk = _extract_routing_key(headers)
                    if rk is None:
                        skipped.append(message.message_id or "<unknown>")
                        await message.nack(requeue=True)
                    elif dry_run:
                        # Don't ack, don't publish — leave message in DLQ.
                        await message.nack(requeue=True)
                    else:
                        await main_ex.publish(
                            aio_pika.Message(
                                body=message.body,
                                headers=headers,
                                message_id=message.message_id,
                                content_type=message.content_type,
                                content_encoding=message.content_encoding,
                                correlation_id=message.correlation_id,
                                reply_to=message.reply_to,
                            ),
                            routing_key=rk,
                        )
                        await message.ack()
                        redriven += 1
                    if limit is not None and requested >= limit:
                        break
            except TimeoutError:
                pass
        _logger.info(
            "dlq_admin.redrive",
            queue=dlq_name,
            dry_run=dry_run,
            requested=requested,
            redriven=redriven,
            skipped=len(skipped),
        )
        return RedriveResult(
            queue=dlq_name,
            dry_run=dry_run,
            requested=requested,
            redriven=redriven,
            skipped=skipped,
        )
    finally:
        if not chan.is_closed:
            await chan.close()
```

- [ ] **Step 4: Run tests, confirm all redrive tests pass**

```bash
uv run pytest tests/mq/test_dlq_admin.py -v
```

Expected: 15 PASS (6 unit + 3 count + 3 peek + 3 redrive).

- [ ] **Step 5: Lint + type + format**

```bash
uv run ruff check app/mq/dlq_admin.py tests/mq/test_dlq_admin.py
uv run ruff format --check app/mq/dlq_admin.py tests/mq/test_dlq_admin.py
uv run mypy app/mq/dlq_admin.py
```

- [ ] **Step 6: Commit**

```bash
git add app/mq/dlq_admin.py tests/mq/test_dlq_admin.py
git commit -m "feat(mq): redrive_dlq re-publishes to main exchange with original routing key"
```

---

## Task 5: Implement `drain_dlq`

**Files:**
- Modify: `app/mq/dlq_admin.py`
- Modify: `tests/mq/test_dlq_admin.py`

- [ ] **Step 1: Append drain slow tests**

```python
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

    # Build sink to prove drained messages are NOT re-published.
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

    # Sink must NOT have received anything.
    chan2 = await rabbitmq_connection.channel()
    sink_q = await chan2.declare_queue(f"{queue}.sink", passive=True)
    assert sink_q.declaration_result.message_count == 0
    await chan2.close()
```

- [ ] **Step 2: Run tests, confirm 2 new drain tests fail**

```bash
uv run pytest tests/mq/test_dlq_admin.py -v
```

- [ ] **Step 3: Implement `drain_dlq`**

Replace `drain_dlq` body in `app/mq/dlq_admin.py`:

```python
async def drain_dlq(
    *,
    connection: aio_pika.abc.AbstractRobustConnection,
    queue: str,
    limit: int | None,
    dry_run: bool = True,
) -> DrainResult:
    dlq_name = f"{queue}.dlq"
    chan = await connection.channel()
    if limit is not None:
        await chan.set_qos(prefetch_count=limit)
    try:
        try:
            dlq = await chan.declare_queue(dlq_name, passive=True)
        except aio_pika.exceptions.ChannelClosed as exc:
            raise DLQNotFoundError(f"queue {dlq_name} does not exist") from exc

        seen = 0
        drained = 0
        async with dlq.iterator(no_ack=False, timeout=2.0) as q_iter:
            try:
                async for message in q_iter:
                    seen += 1
                    if dry_run:
                        await message.nack(requeue=True)
                    else:
                        await message.ack()
                        drained += 1
                    if limit is not None and seen >= limit:
                        break
            except TimeoutError:
                pass
        _logger.info(
            "dlq_admin.drain", queue=dlq_name, dry_run=dry_run, drained=drained, seen=seen,
        )
        return DrainResult(queue=dlq_name, dry_run=dry_run, drained=drained)
    finally:
        if not chan.is_closed:
            await chan.close()
```

- [ ] **Step 4: Run tests, confirm all 17 pass**

```bash
uv run pytest tests/mq/test_dlq_admin.py -v
```

Expected: 17 PASS.

- [ ] **Step 5: Lint + type + format**

```bash
uv run ruff check app/mq/dlq_admin.py tests/mq/test_dlq_admin.py
uv run ruff format --check app/mq/dlq_admin.py tests/mq/test_dlq_admin.py
uv run mypy app/mq/dlq_admin.py
```

- [ ] **Step 6: Commit**

```bash
git add app/mq/dlq_admin.py tests/mq/test_dlq_admin.py
git commit -m "feat(mq): drain_dlq permanently discards DLQ messages"
```

---

## Task 6: CLI `scripts/dlq.py`

**Files:**
- Create: `scripts/dlq.py`
- Modify: `tests/mq/test_dlq_admin.py` (append CLI smoke test)

The CLI is a thin presentation layer over the helpers. argparse subcommands map 1:1 to the helper functions.

- [ ] **Step 1: Create the CLI**

`scripts/dlq.py`:

```python
"""CLI for DLQ admin operations.

Usage:
    uv run python scripts/dlq.py count <queue>
    uv run python scripts/dlq.py peek <queue> [--limit N] [--preview-chars N]
    uv run python scripts/dlq.py redrive <queue> [--limit N | --all] [--apply]
    uv run python scripts/dlq.py drain <queue> [--limit N | --all] [--apply]

`--apply` is required to actually mutate; without it, redrive/drain run as dry-run.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import aio_pika

from app.core.config import Settings
from app.mq.dlq_admin import (
    DLQAdminError,
    DLQNotFoundError,
    count_dlq,
    drain_dlq,
    peek_dlq,
    redrive_dlq,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dlq", description="DLQ admin tools")
    sub = parser.add_subparsers(dest="command", required=True)

    p_count = sub.add_parser("count", help="show DLQ message count")
    p_count.add_argument("queue", help="consumer queue name (DLQ is <queue>.dlq)")

    p_peek = sub.add_parser("peek", help="preview DLQ messages without consuming")
    p_peek.add_argument("queue")
    p_peek.add_argument("--limit", type=int, default=10)
    p_peek.add_argument("--preview-chars", type=int, default=200)

    p_redrive = sub.add_parser("redrive", help="re-publish DLQ messages to main exchange")
    p_redrive.add_argument("queue")
    grp_r = p_redrive.add_mutually_exclusive_group()
    grp_r.add_argument("--limit", type=int, default=None)
    grp_r.add_argument("--all", action="store_true")
    p_redrive.add_argument("--apply", action="store_true", help="actually publish (default is dry-run)")

    p_drain = sub.add_parser("drain", help="permanently discard DLQ messages")
    p_drain.add_argument("queue")
    grp_d = p_drain.add_mutually_exclusive_group()
    grp_d.add_argument("--limit", type=int, default=None)
    grp_d.add_argument("--all", action="store_true")
    p_drain.add_argument("--apply", action="store_true", help="actually discard (default is dry-run)")

    return parser


def _resolve_limit(args: argparse.Namespace) -> int | None:
    if getattr(args, "all", False):
        return None
    return args.limit


async def _run(args: argparse.Namespace) -> int:
    settings = Settings()  # type: ignore[call-arg]
    try:
        conn = await aio_pika.connect_robust(settings.rabbitmq_url)
    except (ConnectionError, OSError) as exc:
        print(f"error: cannot connect to {settings.rabbitmq_url}: {exc}", file=sys.stderr)
        return 3

    try:
        if args.command == "count":
            r = await count_dlq(connection=conn, queue=args.queue)
            print(f"{r.queue}: {r.message_count}")
        elif args.command == "peek":
            msgs = await peek_dlq(
                connection=conn, queue=args.queue, limit=args.limit, preview_chars=args.preview_chars
            )
            print(f"{'event_id':36}  {'routing_key':30}  {'deaths':>6}  preview")
            for m in msgs:
                eid = (m.event_id or "<none>")[:36]
                rk = (m.routing_key or "<none>")[:30]
                preview = m.body_preview.replace("\n", " ")
                print(f"{eid:36}  {rk:30}  {m.death_count:>6}  {preview}")
        elif args.command == "redrive":
            r = await redrive_dlq(
                connection=conn, queue=args.queue, limit=_resolve_limit(args), dry_run=not args.apply
            )
            label = "[dry-run] " if r.dry_run else ""
            print(f"{label}requested={r.requested} redriven={r.redriven} skipped={len(r.skipped)}")
            if r.skipped:
                print(f"  skipped event_ids: {', '.join(r.skipped)}", file=sys.stderr)
        elif args.command == "drain":
            r = await drain_dlq(
                connection=conn, queue=args.queue, limit=_resolve_limit(args), dry_run=not args.apply
            )
            label = "[dry-run] " if r.dry_run else ""
            print(f"{label}drained={r.drained}")
        return 0
    except DLQNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except DLQAdminError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        await conn.close()


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("interrupted; rolled back in-flight messages", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Append a CLI smoke test**

In `tests/mq/test_dlq_admin.py`, append:

```python
import subprocess
import sys as _sys


@pytest.mark.slow
@pytest.mark.asyncio
async def test_cli_count_smoke(rabbitmq_connection, rabbitmq_container, monkeypatch) -> None:
    """Run `scripts/dlq.py count <queue>` against the Testcontainers RabbitMQ and assert output."""
    queue = f"ec.test_cli_count_{uuid.uuid4().hex[:8]}"
    await _declare_dlq(rabbitmq_connection, queue)
    await _publish_to_dlq(rabbitmq_connection, routing_key="ec.x", body=b"x")
    await asyncio.sleep(0.2)

    params = rabbitmq_container.get_connection_params()
    url = f"amqp://{params.credentials.username}:{params.credentials.password}@{params.host}:{params.port}/"

    # Provide a minimal env for Settings() — only rabbitmq_url + the required DB/JWT/OTel fields.
    # Settings reads from env; everything else can be a dummy value.
    env = {
        "RABBITMQ_URL": url,
        "DATABASE_URL": "postgresql+asyncpg://x:x@localhost/x",
        "JWT_PRIVATE_KEY_PATH": "/tmp/x.pem",
        "JWT_PUBLIC_KEY_PATH": "/tmp/x.pem",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317",
    }
    result = subprocess.run(
        [_sys.executable, "scripts/dlq.py", "count", queue],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert f"{queue}.dlq: 1" in result.stdout
```

- [ ] **Step 3: Run all tests**

```bash
uv run pytest tests/mq/test_dlq_admin.py -v
```

Expected: 18 PASS (17 from prior + 1 CLI smoke).

- [ ] **Step 4: Lint + type + format**

```bash
uv run ruff check scripts/dlq.py
uv run ruff format --check scripts/dlq.py
uv run mypy scripts/dlq.py
```

If mypy complains because `scripts/` isn't in the mypy strict scope (currently `app/` only per `pyproject.toml`), that's fine — note in PR body. The CLI was lint-clean.

- [ ] **Step 5: Manual sanity check (optional)**

```bash
uv run python scripts/dlq.py --help
```

Expected: argparse help printout with the 4 subcommands.

- [ ] **Step 6: Commit**

```bash
git add scripts/dlq.py tests/mq/test_dlq_admin.py
git commit -m "feat(scripts): dlq CLI with count/peek/redrive/drain subcommands"
```

---

## Task 7: README documentation + final verification + push + PR

**Files:**
- Modify: `README.md` (add an "Operations: DLQ tools" section)

- [ ] **Step 1: Append to README.md**

Insert before the "Local checks (same as CI)" section (or wherever the table-of-contents flow makes sense):

```markdown
## Operations: DLQ tools

Manage the per-consumer dead-letter queues (`<queue>.dlq`). Run from a shell
with access to RabbitMQ via the `RABBITMQ_URL` env var. The CLI is a thin
wrapper over the reusable helpers in `app/mq/dlq_admin.py`; the same
helpers will back HTTP admin endpoints and monitoring jobs in later work.

```bash
# Show how many messages are stuck.
uv run python scripts/dlq.py count ec.order_consumer

# Inspect up to N messages (non-destructive).
uv run python scripts/dlq.py peek ec.order_consumer --limit 5

# Re-publish to the main exchange (dry-run by default).
uv run python scripts/dlq.py redrive ec.order_consumer --limit 10
uv run python scripts/dlq.py redrive ec.order_consumer --all --apply

# Permanently discard.
uv run python scripts/dlq.py drain ec.order_consumer --limit 10
uv run python scripts/dlq.py drain ec.order_consumer --all --apply
```

Exit codes: `0` ok, `2` queue not found / arg error, `3` AMQP connection failed, `130` SIGINT.
```

- [ ] **Step 2: Commit the README**

```bash
git add README.md
git commit -m "docs: add Operations DLQ tools section to README"
```

- [ ] **Step 3: Run the entire check matrix locally**

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest -m "not slow"
uv run pytest -m slow
```

Expected:
- ruff/format/mypy clean
- pytest unit: existing + new dlq_admin unit tests (around 6-7)
- pytest slow: existing + 12 new dlq_admin slow tests + 1 CLI smoke

- [ ] **Step 4: Inspect commit history**

```bash
git log --oneline origin/main..HEAD
```

Expected: 1 spec commit (already there) + 7 feature commits = 8 commits.

- [ ] **Step 5: Push the branch (user-driven if harness blocks)**

```bash
git push -u origin feature/dlq-admin-tools
```

- [ ] **Step 6: Open the PR**

```bash
gh pr create \
  --base main \
  --head feature/dlq-admin-tools \
  --title "Add DLQ admin tools (CLI + reusable helpers)" \
  --body "$(cat <<'EOF'
## Summary

Implements \`docs/superpowers/specs/2026-05-15-dlq-admin-tools-design.md\`.

- New \`app/mq/dlq_admin.py\` with 4 reusable async helpers:
  - \`count_dlq\` — message count via passive declare
  - \`peek_dlq\` — non-destructive preview (\`nack(requeue=True)\`)
  - \`redrive_dlq\` — re-publish to \`ec.events\` with original routing key (extracted from \`x-death\` header)
  - \`drain_dlq\` — permanently discard
- All return typed dataclasses, raise typed exceptions (\`DLQAdminError\` hierarchy), and emit \`structlog\` structured events.
- New \`scripts/dlq.py\` CLI wraps the helpers. \`--apply\` required to mutate (dry-run is default).
- README: "Operations: DLQ tools" section.

## Why this matters

CLI is the immediate ops tool, but the helper layer is designed so that
later work (C-4 admin HTTP API, scheduled monitoring jobs, Prometheus
metrics) can reuse the same logic without refactoring.

## Test plan

- [x] \`uv run ruff check .\` / \`ruff format --check .\` / \`mypy app\` clean
- [x] \`uv run pytest -m "not slow"\` — 6 new unit tests for the type surface
- [x] \`uv run pytest -m slow\` — 12 new RabbitMQ integration tests covering count/peek/redrive/drain (incl. dry-run, apply, skip-on-no-routing-key)
- [x] CLI smoke test (\`subprocess\` call to \`scripts/dlq.py count\`) passes

## Follow-ups (deferred per spec §8)

- HTTP admin endpoint (\`/admin/dlq/*\`) — C-4 scope
- Web UI — C-5 scope
- Auth / admin role
- Selective redrive by event_id / routing_key
- Prometheus counter (\`ec_dlq_messages_redriven_total\`)
EOF
)"
```

---

## Self-Review Notes

**Spec coverage:**

- §3 architecture diagram (CLI / HTTP / cron all call same helpers) → Task 1 establishes the signatures
- §4 exceptions + dataclasses + 4 functions → Tasks 1 (skeleton) + 2-5 (impls)
- §5 CLI → Task 6
- §6 file structure → matches plan
- §7 unit + slow tests → distributed across Tasks 1-6
- §8 extension points → covered by spec only; no implementation work
- §9 error handling table → CLI maps exceptions to exit codes (Task 6 \_run try/except)
- §10 rollout → Task 7 (README + push + PR)

**Placeholder scan:** No "TBD" / "as appropriate" content. Every function body and every test is fully written.

**Type consistency:**

- `connection: aio_pika.abc.AbstractRobustConnection` is used consistently across all 4 helpers.
- Keyword-only arguments enforced (`*,`) consistently.
- Dataclass field names match between definition (Task 1) and use sites (Tasks 2-6 tests).
- `_extract_routing_key` / `_extract_death_count` / `_body_preview` introduced in Task 3, reused in Task 4.
- `DLQNotFoundError` raised consistently from passive declare failures across count/peek/redrive/drain.
- `dry_run=True` default consistent for `redrive_dlq` and `drain_dlq`.
- Exit codes: `0/2/3/130` consistent between spec §9 and Task 6 CLI.
