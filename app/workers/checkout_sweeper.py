from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import build_engine, build_session_factory
from app.modules.carts.models import Cart, CartStatus

logger = logging.getLogger(__name__)


async def sweep_once(session: AsyncSession, *, timeout_hours: int, limit: int = 100) -> int:
    threshold = datetime.now(tz=timezone.utc) - timedelta(hours=timeout_hours)
    select_stmt = (
        select(Cart.id)
        .where(Cart.status == CartStatus.submitted, Cart.submitted_at < threshold)
        .order_by(Cart.submitted_at)
        .with_for_update(skip_locked=True)
        .limit(limit)
    )
    ids = list((await session.execute(select_stmt)).scalars().all())
    if not ids:
        return 0
    result = await session.execute(
        update(Cart)
        .where(Cart.id.in_(ids), Cart.status == CartStatus.submitted)
        .values(status=CartStatus.failed, failure_reason="timeout")
    )
    return result.rowcount or 0


async def run() -> None:
    settings = get_settings()
    from app.core.telemetry import init_telemetry
    init_telemetry(service_name="ec-checkout-sweeper")
    engine = build_engine(settings.database_url)
    factory = build_session_factory(engine)
    try:
        while True:
            async with factory() as session:
                async with session.begin():
                    n = await sweep_once(session, timeout_hours=settings.checkout_timeout_hours)
                    if n:
                        logger.info("swept %d carts to timeout", n)
            await asyncio.sleep(settings.checkout_sweep_interval_sec)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
