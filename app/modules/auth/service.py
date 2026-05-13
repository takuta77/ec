from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from jose import JWTError

from app.core.exceptions import AuthError, ConflictError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.modules.auth.repository import RefreshTokensRepository
from app.modules.auth.schemas import TokenPair
from app.modules.users.repository import UsersRepository


def _hash_jti(jti: str) -> str:
    return hashlib.sha256(jti.encode()).hexdigest()


class AuthService:
    def __init__(
        self,
        *,
        users: UsersRepository,
        refresh_tokens: RefreshTokensRepository,
        jwt_private_key: str,
        jwt_public_key: str,
        access_ttl_min: int,
        refresh_ttl_days: int,
    ) -> None:
        self.users = users
        self.refresh_tokens = refresh_tokens
        self.priv = jwt_private_key
        self.pub = jwt_public_key
        self.access_ttl = access_ttl_min
        self.refresh_ttl = refresh_ttl_days

    async def register(self, *, email: str, password: str) -> None:
        if await self.users.find_by_email(email):
            raise ConflictError("Email already registered", details={"email": email})
        await self.users.create(email=email, hashed_password=hash_password(password))

    async def login(self, *, email: str, password: str) -> TokenPair:
        user = await self.users.find_by_email(email)
        if user is None or not verify_password(password, user.hashed_password):
            raise AuthError("Invalid credentials")
        return await self._issue_pair(str(user.id))

    async def refresh(self, *, refresh_token: str) -> TokenPair:
        try:
            payload = decode_token(refresh_token, public_key=self.pub)
        except JWTError as e:
            raise AuthError("Invalid refresh token") from e
        if payload.get("typ") != "refresh":
            raise AuthError("Wrong token type")
        jti = payload["jti"]
        sub = payload["sub"]
        stored = await self.refresh_tokens.find_active_by_hash(_hash_jti(jti))
        if stored is None:
            raise AuthError("Refresh token revoked or unknown")
        await self.refresh_tokens.revoke(stored.id)
        return await self._issue_pair(sub)

    async def logout(self, *, refresh_token: str) -> None:
        try:
            payload = decode_token(refresh_token, public_key=self.pub)
        except JWTError:
            return
        jti = payload.get("jti")
        if not jti:
            return
        stored = await self.refresh_tokens.find_active_by_hash(_hash_jti(jti))
        if stored:
            await self.refresh_tokens.revoke(stored.id)

    async def _issue_pair(self, subject: str) -> TokenPair:
        access = create_access_token(
            subject=subject, private_key=self.priv, ttl_min=self.access_ttl
        )
        jti = str(uuid.uuid4())
        refresh = create_refresh_token(
            subject=subject,
            private_key=self.priv,
            ttl_days=self.refresh_ttl,
            jti=jti,
        )
        expires_at = datetime.now(tz=timezone.utc) + timedelta(days=self.refresh_ttl)
        await self.refresh_tokens.store(
            user_id=uuid.UUID(subject),
            token_hash=_hash_jti(jti),
            expires_at=expires_at,
        )
        return TokenPair(access_token=access, refresh_token=refresh)
