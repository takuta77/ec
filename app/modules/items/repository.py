from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.items.models import Item


class ItemsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        name: str,
        price_cents: int,
        currency: str,
        description: str | None = None,
        category: str | None = None,
    ) -> Item:
        item = Item(
            name=name,
            description=description,
            price_cents=price_cents,
            currency=currency,
            category=category,
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def find_by_id(self, item_id: uuid.UUID) -> Item | None:
        return await self.session.get(Item, item_id)

    async def list_active(
        self,
        *,
        limit: int,
        offset: int,
        q: str | None = None,
        category: str | None = None,
    ) -> list[Item]:
        stmt = select(Item).where(Item.is_active.is_(True))
        if q is not None:
            # Escape SQL LIKE wildcards so user input is literal.
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            stmt = stmt.where(
                Item.name.ilike(pattern, escape="\\") | Item.description.ilike(pattern, escape="\\")
            )
        if category is not None:
            stmt = stmt.where(Item.category == category.strip())
        stmt = stmt.order_by(Item.created_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_categories(self) -> list[str]:
        stmt = (
            select(Item.category)
            .where(Item.category.is_not(None))
            .distinct()
            .order_by(Item.category.asc())
        )
        result = await self.session.execute(stmt)
        return [row[0] for row in result.all()]
