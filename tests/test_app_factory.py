from httpx import AsyncClient, ASGITransport
import pytest

from app.main import create_app
from app.core.exceptions import NotFoundError


@pytest.mark.asyncio
async def test_health_ok():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_app_error_handler_returns_envelope():
    app = create_app()

    @app.get("/boom")
    async def boom():
        raise NotFoundError("Thing missing", details={"id": "x"})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.get("/boom")
        assert r.status_code == 404
        body = r.json()
        assert body["error"]["code"] == "not_found"
        assert body["error"]["message"] == "Thing missing"
        assert body["error"]["details"] == {"id": "x"}
