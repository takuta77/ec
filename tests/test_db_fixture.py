import pytest
from sqlalchemy import text


pytestmark = [pytest.mark.slow, pytest.mark.asyncio]


async def test_db_session_fixture(db_session):
    r = await db_session.execute(text("SELECT 1"))
    assert r.scalar() == 1
