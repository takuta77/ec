from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Header
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AuthError
from app.core.security import decode_token
from app.db.session import get_session
from app.modules.users.models import User
from app.modules.users.repository import UsersRepository


async def get_current_user(
    session: Annotated[AsyncSession, Depends(get_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthError("Missing bearer token")
    token = authorization.split(" ", 1)[1]
    settings = get_settings()
    pub = settings.jwt_public_key
    try:
        payload = decode_token(token, public_key=pub)
    except JWTError as e:
        raise AuthError("Invalid token") from e
    if payload.get("typ") != "access":
        raise AuthError("Wrong token type")
    users = UsersRepository(session)
    user = await users.find_by_id(uuid.UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise AuthError("User inactive or missing")
    return user
