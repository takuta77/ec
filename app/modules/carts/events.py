from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app.modules.carts.models import Cart, CartItem
from app.modules.items.models import Item


def build_checkout_requested(
    *,
    cart: Cart,
    lines: list[tuple[CartItem, Item]],
    checkout_request_id: uuid.UUID,
) -> dict[str, Any]:
    items = [
        {
            "item_id": str(item.id),
            "name": item.name,
            "quantity": line.quantity,
            "unit_price_cents": line.unit_price_cents,
            "currency": item.currency,
        }
        for line, item in lines
    ]
    total = sum(line.quantity * line.unit_price_cents for line, _ in lines)
    currency = lines[0][1].currency if lines else "JPY"
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": "checkout.requested",
        "occurred_at": datetime.now(tz=timezone.utc).isoformat(),
        "data": {
            "checkout_request_id": str(checkout_request_id),
            "user_id": str(cart.user_id),
            "cart_id": str(cart.id),
            "items": items,
            "total_cents": total,
            "currency": currency,
        },
    }
