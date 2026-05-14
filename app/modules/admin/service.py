from __future__ import annotations

import aio_pika

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
        raise NotImplementedError

    async def carts_stats(self) -> CartsStats:
        raise NotImplementedError

    async def outbox_stats(self) -> OutboxStats:
        raise NotImplementedError

    async def dlq_stats(self) -> list[DLQQueueStats]:
        raise NotImplementedError

    async def list_carts(
        self, *, status: str | None, limit: int, offset: int
    ) -> list[CartAdminOut]:
        raise NotImplementedError

    async def peek_dlq(self, queue: str, *, limit: int, preview_chars: int) -> list[DLQMessageOut]:
        raise NotImplementedError
