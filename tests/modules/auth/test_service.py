import pytest

from app.modules.auth.service import AuthService
from app.modules.users.repository import UsersRepository
from app.modules.auth.repository import RefreshTokensRepository
from app.core.exceptions import AuthError, ConflictError


pytestmark = [pytest.mark.slow, pytest.mark.asyncio]


@pytest.fixture
def service(db_session, jwt_keys):
    priv, pub = jwt_keys
    return AuthService(
        users=UsersRepository(db_session),
        refresh_tokens=RefreshTokensRepository(db_session),
        jwt_private_key=priv,
        jwt_public_key=pub,
        access_ttl_min=15,
        refresh_ttl_days=14,
    )


async def test_register_and_login(service, db_session):
    await service.register(email="z@example.com", password="pw")
    await db_session.commit()
    pair = await service.login(email="z@example.com", password="pw")
    await db_session.commit()
    assert pair.access_token and pair.refresh_token


async def test_register_duplicate(service, db_session):
    await service.register(email="dup@example.com", password="pw")
    await db_session.commit()
    with pytest.raises(ConflictError):
        await service.register(email="dup@example.com", password="pw")


async def test_login_wrong_password(service, db_session):
    await service.register(email="wp@example.com", password="pw")
    await db_session.commit()
    with pytest.raises(AuthError):
        await service.login(email="wp@example.com", password="bad")


async def test_refresh_rotates(service, db_session):
    await service.register(email="rr@example.com", password="pw")
    await db_session.commit()
    pair = await service.login(email="rr@example.com", password="pw")
    await db_session.commit()
    new_pair = await service.refresh(refresh_token=pair.refresh_token)
    await db_session.commit()
    assert new_pair.refresh_token != pair.refresh_token
    with pytest.raises(AuthError):
        await service.refresh(refresh_token=pair.refresh_token)
