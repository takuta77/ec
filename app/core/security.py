from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import jwt
from passlib.context import CryptContext


_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return _pwd.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd.verify(plain, hashed)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def create_access_token(*, subject: str, private_key: str, ttl_min: int) -> str:
    payload: dict[str, Any] = {
        "sub": subject,
        "typ": "access",
        "iat": int(_now().timestamp()),
        "exp": int((_now() + timedelta(minutes=ttl_min)).timestamp()),
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


def create_refresh_token(
    *, subject: str, private_key: str, ttl_days: int, jti: str | None = None
) -> str:
    payload: dict[str, Any] = {
        "sub": subject,
        "typ": "refresh",
        "jti": jti or str(uuid.uuid4()),
        "iat": int(_now().timestamp()),
        "exp": int((_now() + timedelta(days=ttl_days)).timestamp()),
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


def decode_token(token: str, *, public_key: str) -> dict[str, Any]:
    return jwt.decode(token, public_key, algorithms=["RS256"])
