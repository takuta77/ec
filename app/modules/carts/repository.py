from __future__ import annotations

import uuid
from typing import Any, cast

from sqlalchemy import select, delete, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.carts.models import Cart, CartItem, CartStatus


class CartsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_open_for_user(self, user_id: uuid.UUID) -> Cart | None:
        stmt = select(Cart).where(Cart.user_id == user_id, Cart.status == CartStatus.open)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_open_for_update(self, cart_id: uuid.UUID, user_id: uuid.UUID) -> Cart | None:
        stmt = (
            select(Cart)
            .where(Cart.id == cart_id, Cart.user_id == user_id, Cart.status == CartStatus.open)
            .with_for_update()
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def create_open(self, user_id: uuid.UUID) -> Cart:
        cart = Cart(user_id=user_id, status=CartStatus.open)
        self.session.add(cart)
        await self.session.flush()
        return cart

    async def upsert_line(
        self, cart_id: uuid.UUID, item_id: uuid.UUID, quantity: int, unit_price_cents: int
    ) -> CartItem:
        stmt = select(CartItem).where(CartItem.cart_id == cart_id, CartItem.item_id == item_id)
        existing = (await self.session.execute(stmt)).scalar_one_or_none()
        if existing:
            existing.quantity = quantity
            existing.unit_price_cents = unit_price_cents
            await self.session.flush()
            return existing
        line = CartItem(
            cart_id=cart_id, item_id=item_id, quantity=quantity, unit_price_cents=unit_price_cents
        )
        self.session.add(line)
        await self.session.flush()
        return line

    async def delete_line(self, cart_id: uuid.UUID, item_id: uuid.UUID) -> int:
        result = await self.session.execute(
            delete(CartItem).where(CartItem.cart_id == cart_id, CartItem.item_id == item_id)
        )
        return cast(CursorResult[Any], result).rowcount or 0

    async def list_lines(self, cart_id: uuid.UUID) -> list[CartItem]:
        result = await self.session.execute(select(CartItem).where(CartItem.cart_id == cart_id))
        return list(result.scalars().all())

    async def transition_on_order_result(
        self,
        *,
        checkout_request_id: uuid.UUID,
        new_status: CartStatus,
        order_id: uuid.UUID | None,
        failure_reason: str | None,
    ) -> int:
        stmt = (
            update(Cart)
            .where(
                Cart.checkout_request_id == checkout_request_id, Cart.status == CartStatus.submitted
            )
            .values(status=new_status, order_id=order_id, failure_reason=failure_reason)
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult[Any], result).rowcount or 0
