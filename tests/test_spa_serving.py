from __future__ import annotations

import pytest


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    monkeypatch.setenv("RABBITMQ_URL", "amqp://guest:guest@localhost/")
    monkeypatch.setenv("JWT_PRIVATE_KEY_PATH", "/tmp/x.pem")
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", "/tmp/x.pem")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    monkeypatch.delenv("SERVE_FRONTEND", raising=False)
    monkeypatch.delenv("FRONTEND_DIST_PATH", raising=False)

    from app.core.config import Settings

    s = Settings()  # type: ignore[call-arg]
    assert s.serve_frontend is False
    assert s.frontend_dist_path == "frontend/dist"


def test_settings_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    monkeypatch.setenv("RABBITMQ_URL", "amqp://guest:guest@localhost/")
    monkeypatch.setenv("JWT_PRIVATE_KEY_PATH", "/tmp/x.pem")
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", "/tmp/x.pem")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    monkeypatch.setenv("SERVE_FRONTEND", "true")
    monkeypatch.setenv("FRONTEND_DIST_PATH", "/custom/dist")

    from app.core.config import Settings

    s = Settings()  # type: ignore[call-arg]
    assert s.serve_frontend is True
    assert s.frontend_dist_path == "/custom/dist"
