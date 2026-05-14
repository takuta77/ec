from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class CartAdminRow:
    id: uuid.UUID
    user_id: uuid.UUID
    status: str
    failure_reason: str | None
    submitted_at: datetime | None
    line_count: int
    created_at: datetime
    updated_at: datetime


class AdminRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def items_stats(self) -> tuple[int, int, dict[str, int]]:
        raise NotImplementedError

    async def carts_stats(self) -> tuple[dict[str, int], int]:
        raise NotImplementedError

    async def outbox_stats(self) -> tuple[int, int, datetime | None]:
        raise NotImplementedError

    async def list_carts(
        self,
        *,
        status: str | None,
        limit: int,
        offset: int,
    ) -> list[CartAdminRow]:
        raise NotImplementedError
