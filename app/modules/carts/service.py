from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.core.exceptions import ConflictError, NotFoundError
from app.modules.carts.events import build_checkout_requested
from app.modules.carts.models import Cart, CartItem, CartStatus
from app.modules.carts.repository import CartsRepository
from app.modules.carts.schemas import CheckoutOut
from app.modules.items.models import Item
from app.modules.items.repository import ItemsRepository
from app.modules.outbox.repository import OutboxRepository


class CartsService:
    def __init__(
        self,
        carts: CartsRepository,
        items: ItemsRepository,
        outbox: OutboxRepository | None = None,
    ) -> None:
        self.carts = carts
        self.items = items
        self.outbox = outbox

    async def open_or_get(self, user_id: uuid.UUID) -> Cart:
        existing = await self.carts.get_open_for_user(user_id)
        if existing:
            return existing
        return await self.carts.create_open(user_id)

    async def add_item(
        self, *, user_id: uuid.UUID, item_id: uuid.UUID, quantity: int
    ) -> tuple[Cart, CartItem]:
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

    async def submit_checkout(
        self, *, user_id: uuid.UUID, traceparent: str | None = None
    ) -> CheckoutOut:
        if self.outbox is None:
            raise RuntimeError("outbox repository not bound")
        cart = await self.carts.get_open_for_user(user_id)
        if cart is None:
            raise NotFoundError("Open cart not found")
        cart_locked = await self.carts.get_open_for_update(cart.id, user_id)
        if cart_locked is None:
            raise ConflictError("Cart not open")
        lines = await self.carts.list_lines(cart_locked.id)
        if not lines:
            raise ConflictError("Cart is empty")
        line_items: list[tuple[CartItem, Item]] = []
        for line in lines:
            item = await self.items.find_by_id(line.item_id)
            if item is None:
                raise NotFoundError("Item not found", details={"item_id": str(line.item_id)})
            line_items.append((line, item))

        checkout_request_id = uuid.uuid4()
        cart_locked.status = CartStatus.submitted
        cart_locked.checkout_request_id = checkout_request_id
        cart_locked.submitted_at = datetime.now(tz=timezone.utc)

        envelope = build_checkout_requested(
            cart=cart_locked,
            lines=line_items,
            checkout_request_id=checkout_request_id,
        )
        headers = {"traceparent": traceparent} if traceparent else {}
        await self.outbox.append(
            aggregate_type="cart",
            aggregate_id=cart_locked.id,
            event_type="checkout.requested",
            payload=envelope,
            headers=headers,
        )
        return CheckoutOut(checkout_request_id=checkout_request_id)

    async def apply_order_result(
        self,
        *,
        event_type: str,
        checkout_request_id: uuid.UUID,
        order_id: uuid.UUID | None,
        failure_reason: str | None,
    ) -> int:
        new_status = CartStatus.ordered if event_type == "order.created" else CartStatus.failed
        return await self.carts.transition_on_order_result(
            checkout_request_id=checkout_request_id,
            new_status=new_status,
            order_id=order_id,
            failure_reason=failure_reason,
        )

    async def cancel_my_open_cart(self, *, user_id: uuid.UUID) -> uuid.UUID:
        cart_id = await self.carts.cancel_open(user_id)
        if cart_id is None:
            raise NotFoundError(
                "No open cart to cancel",
                details={"user_id": str(user_id)},
            )
        return cart_id

    async def reopen_my_cart(self, *, user_id: uuid.UUID) -> Cart:
        affected = await self.carts.reopen_failed_timeout(user_id)
        if affected == 0:
            raise NotFoundError(
                "No reopenable cart found",
                details={"user_id": str(user_id)},
            )
        cart = await self.carts.get_open_for_user(user_id)
        assert cart is not None
        return cart
