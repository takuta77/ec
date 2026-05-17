from __future__ import annotations

import aio_pika

from app.mq.dlq_admin import DLQNotFoundError, count_dlq, peek_dlq
from app.mq.queues import KNOWN_CONSUMER_QUEUES
from app.modules.admin.repository import AdminRepository
from app.modules.admin.schemas import (
    CartAdminOut,
    CartsStats,
    DLQMessageOut,
    DLQQueueStats,
    ItemsStats,
    OutboxStats,
)


class MQConnectionUnavailable(Exception):
    """Raised when admin requires MQ but the lifespan-managed connection is None."""


class AdminService:
    def __init__(
        self,
        repo: AdminRepository,
        mq_connection: aio_pika.abc.AbstractRobustConnection | None,
    ) -> None:
        self.repo = repo
        self.mq_connection = mq_connection

    async def items_stats(self) -> ItemsStats:
        total, active, by_category = await self.repo.items_stats()
        return ItemsStats(total=total, active=active, by_category=by_category)

    async def carts_stats(self) -> CartsStats:
        by_status, failed_timeout = await self.repo.carts_stats()
        return CartsStats(by_status=by_status, failed_with_timeout=failed_timeout)

    async def outbox_stats(self) -> OutboxStats:
        pending, dispatched, oldest = await self.repo.outbox_stats()
        return OutboxStats(pending=pending, dispatched=dispatched, oldest_pending_at=oldest)

    async def dlq_stats(self) -> list[DLQQueueStats]:
        if self.mq_connection is None:
            raise MQConnectionUnavailable
        results: list[DLQQueueStats] = []
        for queue in KNOWN_CONSUMER_QUEUES:
            try:
                r = await count_dlq(connection=self.mq_connection, queue=queue)
                results.append(DLQQueueStats(queue=r.queue, message_count=r.message_count))
            except DLQNotFoundError:
                results.append(DLQQueueStats(queue=f"{queue}.dlq", message_count=0))
        return results

    async def list_carts(
        self, *, status: str | None, limit: int, offset: int
    ) -> list[CartAdminOut]:
        rows = await self.repo.list_carts(status=status, limit=limit, offset=offset)
        return [
            CartAdminOut(
                id=r.id,
                user_id=r.user_id,
                status=r.status,
                failure_reason=r.failure_reason,
                submitted_at=r.submitted_at,
                line_count=r.line_count,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ]

    async def peek_dlq(self, queue: str, *, limit: int, preview_chars: int) -> list[DLQMessageOut]:
        if self.mq_connection is None:
            raise MQConnectionUnavailable
        msgs = await peek_dlq(
            connection=self.mq_connection,
            queue=queue,
            limit=limit,
            preview_chars=preview_chars,
        )
        return [
            DLQMessageOut(
                event_id=m.event_id,
                routing_key=m.routing_key,
                death_count=m.death_count,
                body_preview=m.body_preview,
            )
            for m in msgs
        ]
