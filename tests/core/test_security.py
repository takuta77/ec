from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
)


def test_hash_and_verify_password():
    h = hash_password("hunter2")
    assert verify_password("hunter2", h) is True
    assert verify_password("wrong", h) is False


def test_jwt_roundtrip(jwt_keys):
    priv, pub = jwt_keys
    token = create_access_token(subject="user-1", private_key=priv, ttl_min=5)
    payload = decode_token(token, public_key=pub)
    assert payload["sub"] == "user-1"
    assert payload["typ"] == "access"


def test_refresh_token_has_jti(jwt_keys):
    priv, pub = jwt_keys
    token = create_refresh_token(subject="user-1", private_key=priv, ttl_days=14)
    payload = decode_token(token, public_key=pub)
    assert payload["typ"] == "refresh"
    assert "jti" in payload
