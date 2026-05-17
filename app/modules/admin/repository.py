from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.carts.models import Cart, CartItem, CartStatus
from app.modules.items.models import Item
from app.modules.outbox.models import OutboxEvent


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


_ALL_CART_STATUSES = ("open", "submitted", "ordered", "failed", "cancelled")


class AdminRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def items_stats(self) -> tuple[int, int, dict[str, int]]:
        total = (await self.session.execute(select(func.count(Item.id)))).scalar_one()
        active = (
            await self.session.execute(select(func.count(Item.id)).where(Item.is_active.is_(True)))
        ).scalar_one()
        by_cat_rows = (
            await self.session.execute(
                select(Item.category, func.count(Item.id))
                .where(Item.is_active.is_(True), Item.category.is_not(None))
                .group_by(Item.category)
                .order_by(Item.category.asc())
            )
        ).all()
        by_category = {row[0]: row[1] for row in by_cat_rows}
        return total, active, by_category

    async def carts_stats(self) -> tuple[dict[str, int], int]:
        rows = (
            await self.session.execute(
                select(Cart.status, func.count(Cart.id)).group_by(Cart.status)
            )
        ).all()
        seen = {(row[0].value if hasattr(row[0], "value") else str(row[0])): row[1] for row in rows}
        by_status = {s: seen.get(s, 0) for s in _ALL_CART_STATUSES}

        failed_timeout = (
            await self.session.execute(
                select(func.count(Cart.id)).where(
                    Cart.status == CartStatus.failed, Cart.failure_reason == "timeout"
                )
            )
        ).scalar_one()
        return by_status, failed_timeout

    async def outbox_stats(self) -> tuple[int, int, datetime | None]:
        # pending: not yet published, not dead-lettered.
        pending = (
            await self.session.execute(
                select(func.count(OutboxEvent.id)).where(
                    OutboxEvent.published_at.is_(None),
                    OutboxEvent.dead_letter_at.is_(None),
                )
            )
        ).scalar_one()
        # dispatched: successfully published.
        dispatched = (
            await self.session.execute(
                select(func.count(OutboxEvent.id)).where(OutboxEvent.published_at.is_not(None))
            )
        ).scalar_one()
        oldest = (
            await self.session.execute(
                select(func.min(OutboxEvent.created_at)).where(
                    OutboxEvent.published_at.is_(None),
                    OutboxEvent.dead_letter_at.is_(None),
                )
            )
        ).scalar_one()
        return pending, dispatched, oldest

    async def list_carts(
        self,
        *,
        status: str | None,
        limit: int,
        offset: int,
    ) -> list[CartAdminRow]:
        line_count_subq = (
            select(func.count(CartItem.cart_id).cast(Integer))
            .where(CartItem.cart_id == Cart.id)
            .correlate(Cart)
            .scalar_subquery()
            .label("line_count")
        )
        stmt = (
            select(
                Cart.id,
                Cart.user_id,
                Cart.status,
                Cart.failure_reason,
                Cart.submitted_at,
                line_count_subq,
                Cart.created_at,
                Cart.updated_at,
            )
            .order_by(Cart.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if status is not None:
            stmt = stmt.where(Cart.status == CartStatus(status))
        rows = (await self.session.execute(stmt)).all()
        return [
            CartAdminRow(
                id=r[0],
                user_id=r[1],
                status=r[2].value if hasattr(r[2], "value") else str(r[2]),
                failure_reason=r[3],
                submitted_at=r[4],
                line_count=r[5] or 0,
                created_at=r[6],
                updated_at=r[7],
            )
            for r in rows
        ]
