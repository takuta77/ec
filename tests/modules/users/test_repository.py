import pytest

from app.modules.users.repository import UsersRepository
from app.modules.users.models import User


pytestmark = [pytest.mark.slow, pytest.mark.asyncio]


async def test_create_and_find_by_email(db_session):
    repo = UsersRepository(db_session)
    user = await repo.create(email="alice@example.com", hashed_password="hash")
    await db_session.commit()
    assert isinstance(user, User)

    found = await repo.find_by_email("alice@example.com")
    assert found is not None and found.id == user.id


async def test_duplicate_email_raises(db_session):
    repo = UsersRepository(db_session)
    await repo.create(email="b@example.com", hashed_password="h")
    await db_session.commit()
    with pytest.raises(Exception):
        await repo.create(email="b@example.com", hashed_password="h")
        await db_session.commit()
