from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
import asyncio

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from testcontainers.postgres import PostgresContainer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base


@pytest.fixture(scope="session")
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    with PostgresContainer("postgres:16") as pg:
        yield pg


@pytest.fixture(scope="session")
def database_url(postgres_container: PostgresContainer) -> str:
    return postgres_container.get_connection_url().replace("psycopg2", "asyncpg")


@pytest.fixture
async def db_session(database_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(database_url, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture(scope="session")
def jwt_keys() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    pub = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return priv, pub


@pytest.fixture
async def app_with_db(database_url, jwt_keys, monkeypatch):
    priv, pub = jwt_keys
    from pathlib import Path
    import tempfile

    priv_path = Path(tempfile.mkstemp(suffix=".pem")[1])
    priv_path.write_text(priv)
    pub_path = Path(tempfile.mkstemp(suffix=".pem")[1])
    pub_path.write_text(pub)

    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("RABBITMQ_URL", "amqp://guest:guest@localhost/")
    monkeypatch.setenv("JWT_PRIVATE_KEY_PATH", str(priv_path))
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", str(pub_path))
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

    from app.core.config import get_settings

    get_settings.cache_clear()

    # NOTE: Importing create_app forces registration of every module's
    # ORM models onto Base.metadata via router imports. This MUST happen
    # before Base.metadata.create_all so all tables get created.
    from app.main import create_app
    from app.db.session import init_engine, dispose_engine
    from app.db.base import Base
    from sqlalchemy.ext.asyncio import create_async_engine

    e = create_async_engine(database_url, future=True)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await e.dispose()

    init_engine()
    app = create_app()
    yield app
    await dispose_engine()

    e = create_async_engine(database_url, future=True)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await e.dispose()
