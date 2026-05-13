from __future__ import annotations

import uuid

from app.core.exceptions import NotFoundError
from app.modules.users.models import User
from app.modules.users.repository import UsersRepository


class UsersService:
    def __init__(self, users: UsersRepository) -> None:
        self.users = users

    async def get(self, user_id: uuid.UUID) -> User:
        u = await self.users.find_by_id(user_id)
        if u is None:
            raise NotFoundError("User not found", details={"user_id": str(user_id)})
        return u
