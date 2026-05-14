from __future__ import annotations

import uuid

from app.core.exceptions import NotFoundError
from app.modules.items.models import Item
from app.modules.items.repository import ItemsRepository


class ItemsService:
    def __init__(self, items: ItemsRepository) -> None:
        self.items = items

    async def create(self, *, name: str, price_cents: int, currency: str, description: str | None = None) -> Item:
        return await self.items.create(name=name, price_cents=price_cents, currency=currency, description=description)

    async def get(self, item_id: uuid.UUID) -> Item:
        i = await self.items.find_by_id(item_id)
        if i is None or not i.is_active:
            raise NotFoundError("Item not found", details={"item_id": str(item_id)})
        return i

    async def list_active(self, *, limit: int, offset: int) -> list[Item]:
        return await self.items.list_active(limit=limit, offset=offset)
