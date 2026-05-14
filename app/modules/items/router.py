from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.items.models import Item
from app.modules.items.repository import ItemsRepository
from app.modules.items.schemas import ItemOut
from app.modules.items.service import ItemsService


router = APIRouter(prefix="/items", tags=["items"])


def _service(session: AsyncSession) -> ItemsService:
    return ItemsService(ItemsRepository(session))


@router.get("", response_model=list[ItemOut])
async def list_items(
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    category: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[Item]:
    return await _service(session).list_active(limit=limit, offset=offset, q=q, category=category)


@router.get("/categories")
async def list_categories(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, list[str]]:
    return {"categories": await _service(session).list_categories()}


@router.get("/{item_id}", response_model=ItemOut)
async def get_item(
    item_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Item:
    return await _service(session).get(item_id)
