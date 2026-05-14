from __future__ import annotations

from typing import Annotated

import aio_pika
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.admin.dependencies import require_admin
from app.modules.admin.repository import AdminRepository
from app.modules.admin.schemas import (
    CartAdminOut,
    CartsStats,
    DLQMessageOut,
    DLQQueueStats,
    ItemsStats,
    OutboxStats,
)
from app.mq.dlq_admin import DLQNotFoundError
from app.modules.admin.service import AdminService, MQConnectionUnavailable
from app.mq.connection import get_mq_connection


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _service(
    session: AsyncSession,
    mq_connection: aio_pika.abc.AbstractRobustConnection | None,
) -> AdminService:
    return AdminService(AdminRepository(session), mq_connection)


@router.get("/stats/items", response_model=ItemsStats)
async def stats_items(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ItemsStats:
    return await _service(session, None).items_stats()


@router.get("/stats/carts", response_model=CartsStats)
async def stats_carts(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CartsStats:
    return await _service(session, None).carts_stats()


@router.get("/stats/outbox", response_model=OutboxStats)
async def stats_outbox(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OutboxStats:
    return await _service(session, None).outbox_stats()


@router.get("/stats/dlq", response_model=list[DLQQueueStats])
async def stats_dlq(
    session: Annotated[AsyncSession, Depends(get_session)],
    mq_connection: Annotated[
        aio_pika.abc.AbstractRobustConnection | None, Depends(get_mq_connection)
    ],
) -> list[DLQQueueStats]:
    try:
        return await _service(session, mq_connection).dlq_stats()
    except MQConnectionUnavailable as exc:
        raise HTTPException(status_code=503, detail="MQ unavailable") from exc


@router.get("/carts", response_model=list[CartAdminOut])
async def list_carts(
    session: Annotated[AsyncSession, Depends(get_session)],
    status: Annotated[str | None, Query(min_length=1, max_length=20)] = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[CartAdminOut]:
    return await _service(session, None).list_carts(status=status, limit=limit, offset=offset)


@router.get("/dlq/{queue}/peek", response_model=list[DLQMessageOut])
async def peek_dlq_route(
    queue: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    mq_connection: Annotated[
        aio_pika.abc.AbstractRobustConnection | None, Depends(get_mq_connection)
    ],
    limit: int = Query(default=10, ge=1, le=100),
    preview_chars: int = Query(default=200, ge=10, le=2000),
) -> list[DLQMessageOut]:
    try:
        return await _service(session, mq_connection).peek_dlq(
            queue, limit=limit, preview_chars=preview_chars
        )
    except DLQNotFoundError as exc:
        raise HTTPException(status_code=404, detail="dlq_not_found") from exc
    except MQConnectionUnavailable as exc:
        raise HTTPException(status_code=503, detail="MQ unavailable") from exc
