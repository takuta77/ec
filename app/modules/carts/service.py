from __future__ import annotations

import uuid

from app.core.exceptions import NotFoundError
from app.modules.carts.models import Cart, CartItem
from app.modules.carts.repository import CartsRepository
from app.modules.items.repository import ItemsRepository


class CartsService:
    def __init__(self, carts: CartsRepository, items: ItemsRepository) -> None:
        self.carts = carts
        self.items = items

    async def open_or_get(self, user_id: uuid.UUID) -> Cart:
        existing = await self.carts.get_open_for_user(user_id)
        if existing:
            return existing
        return await self.carts.create_open(user_id)

    async def add_item(self, *, user_id: uuid.UUID, item_id: uuid.UUID, quantity: int) -> tuple[Cart, CartItem]:
        cart = await self.open_or_get(user_id)
        item = await self.items.find_by_id(item_id)
        if item is None or not item.is_active:
            raise NotFoundError("Item not found", details={"item_id": str(item_id)})
        line = await self.carts.upsert_line(cart.id, item.id, quantity, item.price_cents)
        return cart, line

    async def remove_item(self, *, user_id: uuid.UUID, item_id: uuid.UUID) -> Cart:
        cart = await self.carts.get_open_for_user(user_id)
        if cart is None:
            raise NotFoundError("Open cart not found")
        deleted = await self.carts.delete_line(cart.id, item_id)
        if deleted == 0:
            raise NotFoundError("Cart line not found", details={"item_id": str(item_id)})
        return cart
