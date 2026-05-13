import pytest
from testcontainers.postgres import PostgresContainer
from sqlalchemy import text

from app.db.session import build_engine, build_session_factory


pytestmark = pytest.mark.slow


@pytest.mark.asyncio
async def test_session_factory_executes_query():
    with PostgresContainer("postgres:16") as pg:
        url = pg.get_connection_url().replace("psycopg2", "asyncpg")
        engine = build_engine(url)
        factory = build_session_factory(engine)
        async with factory() as session:
            result = await session.execute(text("SELECT 1"))
            assert result.scalar() == 1
        await engine.dispose()
