from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_session
from app.modules.auth.repository import RefreshTokensRepository
from app.modules.auth.schemas import LoginIn, RefreshIn, RegisterIn, TokenPair
from app.modules.auth.service import AuthService
from app.modules.users.repository import UsersRepository


router = APIRouter(prefix="/auth", tags=["auth"])


def _service(session: AsyncSession) -> AuthService:
    s = get_settings()
    return AuthService(
        users=UsersRepository(session),
        refresh_tokens=RefreshTokensRepository(session),
        jwt_private_key=s.jwt_private_key,
        jwt_public_key=s.jwt_public_key,
        access_ttl_min=s.jwt_access_ttl_min,
        refresh_ttl_days=s.jwt_refresh_ttl_days,
    )


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    await _service(session).register(email=payload.email, password=payload.password)
    await session.commit()
    return {"status": "registered"}


@router.post("/login", response_model=TokenPair)
async def login(
    payload: LoginIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenPair:
    pair = await _service(session).login(email=payload.email, password=payload.password)
    await session.commit()
    return pair


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    payload: RefreshIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenPair:
    pair = await _service(session).refresh(refresh_token=payload.refresh_token)
    await session.commit()
    return pair


@router.post("/logout")
async def logout(
    payload: RefreshIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    await _service(session).logout(refresh_token=payload.refresh_token)
    await session.commit()
    return {"status": "logged_out"}
