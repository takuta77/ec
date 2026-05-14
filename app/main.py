from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aio_pika
import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.config import Settings
from app.core.exceptions import AppError
from app.db.session import dispose_engine, init_engine
from app.modules.auth.router import router as auth_router
from app.modules.carts.router import router as cart_router
from app.modules.items.router import router as items_router
from app.modules.users.router import router as users_router
from app.mq.connection import open_connection
from app.shared.responses import error_envelope


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    from app.core.logging import configure_structlog
    from app.core.telemetry import init_telemetry, instrument_fastapi

    configure_structlog()
    init_telemetry(service_name="ec-api")
    instrument_fastapi(app)
    init_engine()
    settings_for_mq = Settings()  # type: ignore[call-arg]  # populated from env at runtime
    mq_connection: aio_pika.abc.AbstractRobustConnection | None = None
    try:
        mq_connection = await open_connection(settings_for_mq.rabbitmq_url)
    except Exception:
        structlog.get_logger("ec.lifespan").warning("mq_connection_unavailable")
    app.state.mq_connection = mq_connection
    try:
        yield
    finally:
        if mq_connection is not None:
            await mq_connection.close()
        await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(title="EC API", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content=error_envelope(
                code=exc.code,
                message=exc.message,
                details=exc.details,
                trace_id=None,
            ),
        )

    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(items_router)
    app.include_router(cart_router)
    from app.modules.admin.router import router as admin_router

    app.include_router(admin_router)
    return app


app = create_app()
