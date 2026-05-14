from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.items.models import Item


class ItemsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self, *, name: str, price_cents: int, currency: str, description: str | None = None
    ) -> Item:
        item = Item(name=name, description=description, price_cents=price_cents, currency=currency)
        self.session.add(item)
        await self.session.flush()
        return item

    async def find_by_id(self, item_id: uuid.UUID) -> Item | None:
        return await self.session.get(Item, item_id)

    async def list_active(self, *, limit: int, offset: int) -> list[Item]:
        stmt = (
            select(Item)
            .where(Item.is_active.is_(True))
            .order_by(Item.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
