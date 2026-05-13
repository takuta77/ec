from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.exceptions import AppError
from app.db.session import init_engine, dispose_engine
from app.modules.auth.router import router as auth_router
from app.modules.users.router import router as users_router
from app.shared.responses import error_envelope


def create_app() -> FastAPI:
    app = FastAPI(title="EC API")

    @app.on_event("startup")
    async def _startup() -> None:
        init_engine()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await dispose_engine()

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content=error_envelope(code=exc.code, message=exc.message, details=exc.details, trace_id=None),
        )

    app.include_router(auth_router)
    app.include_router(users_router)
    return app


app = create_app()
