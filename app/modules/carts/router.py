from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.auth.dependencies import get_current_user
from app.modules.carts.repository import CartsRepository
from app.modules.carts.schemas import AddItemIn, CartLineOut, CartOut, CheckoutOut
from app.modules.carts.service import CartsService
from app.modules.items.repository import ItemsRepository
from app.modules.outbox.repository import OutboxRepository
from app.modules.users.models import User


router = APIRouter(prefix="/cart", tags=["cart"])


def _service(session: AsyncSession) -> CartsService:
    return CartsService(
        CartsRepository(session),
        ItemsRepository(session),
        outbox=OutboxRepository(session),
    )


async def _cart_with_lines(session: AsyncSession, cart) -> CartOut:
    repo = CartsRepository(session)
    lines = await repo.list_lines(cart.id)
    return CartOut(
        id=cart.id,
        status=cart.status.value,
        failure_reason=cart.failure_reason,
        lines=[
            CartLineOut(item_id=l.item_id, quantity=l.quantity, unit_price_cents=l.unit_price_cents)
            for l in lines
        ],
    )


@router.get("", response_model=CartOut)
async def get_my_cart(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CartOut:
    cart = await _service(session).open_or_get(user.id)
    await session.commit()
    return await _cart_with_lines(session, cart)


@router.post("/items", response_model=CartOut)
async def add_item(
    payload: AddItemIn,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CartOut:
    cart, _ = await _service(session).add_item(
        user_id=user.id, item_id=payload.item_id, quantity=payload.quantity
    )
    await session.commit()
    return await _cart_with_lines(session, cart)


@router.delete("/items/{item_id}", response_model=CartOut)
async def remove_item(
    item_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CartOut:
    cart = await _service(session).remove_item(user_id=user.id, item_id=item_id)
    await session.commit()
    return await _cart_with_lines(session, cart)


@router.post("/checkout", response_model=CheckoutOut, status_code=202)
async def checkout(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CheckoutOut:
    result = await _service(session).submit_checkout(user_id=user.id)
    await session.commit()
    return result


@router.post("/cancel")
async def cancel(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    cart_id = await _service(session).cancel_my_open_cart(user_id=user.id)
    await session.commit()
    return {"status": "cancelled", "cart_id": str(cart_id)}


@router.post("/reopen", response_model=CartOut)
async def reopen(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CartOut:
    cart = await _service(session).reopen_my_cart(user_id=user.id)
    await session.commit()
    return await _cart_with_lines(session, cart)
