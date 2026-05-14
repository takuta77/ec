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
    from app.mq.dlq_admin import count_dlq, drain_dlq, peek_dlq, redrive_dlq

    with pytest.raises(NotImplementedError):
        await count_dlq(connection=None, queue="x")  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError):
        await peek_dlq(connection=None, queue="x", limit=1)  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError):
        await redrive_dlq(connection=None, queue="x", limit=1)  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError):
        await drain_dlq(connection=None, queue="x", limit=1)  # type: ignore[arg-type]
