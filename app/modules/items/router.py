from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.items.repository import ItemsRepository
from app.modules.items.schemas import ItemOut
from app.modules.items.service import ItemsService


router = APIRouter(prefix="/items", tags=["items"])


def _service(session: AsyncSession) -> ItemsService:
    return ItemsService(ItemsRepository(session))


@router.get("", response_model=list[ItemOut])
async def list_items(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list:
    return await _service(session).list_active(limit=limit, offset=offset)


@router.get("/{item_id}", response_model=ItemOut)
async def get_item(
    item_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    return await _service(session).get(item_id)
