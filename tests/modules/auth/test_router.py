import pytest
from httpx import AsyncClient, ASGITransport


pytestmark = [pytest.mark.slow, pytest.mark.asyncio]


async def test_register_login_me(app_with_db, db_session):
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.post("/auth/register", json={"email": "u@example.com", "password": "pw"})
        assert r.status_code == 201

        r = await c.post("/auth/login", json={"email": "u@example.com", "password": "pw"})
        assert r.status_code == 200
        tokens = r.json()
        assert tokens["access_token"]

        r = await c.get("/users/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
        assert r.status_code == 200
        assert r.json()["email"] == "u@example.com"

        r = await c.get("/users/me")
        assert r.status_code == 401
