from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.exceptions import AppError
from app.shared.responses import error_envelope


def create_app() -> FastAPI:
    app = FastAPI(title="EC API")

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

    return app


app = create_app()
