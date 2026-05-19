from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient


def _base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    monkeypatch.setenv("RABBITMQ_URL", "amqp://guest:guest@localhost/")
    monkeypatch.setenv("JWT_PRIVATE_KEY_PATH", "/tmp/x.pem")
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", "/tmp/x.pem")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")


def _fresh_app(monkeypatch: pytest.MonkeyPatch):
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    return create_app()


def test_spa_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.delenv("SERVE_FRONTEND", raising=False)
    app = _fresh_app(monkeypatch)
    with TestClient(app) as client:
        r = client.get("/admin/ui")
    assert r.status_code == 404


def test_spa_enabled_but_dist_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("SERVE_FRONTEND", "true")
    monkeypatch.setenv("FRONTEND_DIST_PATH", str(tmp_path / "does-not-exist"))
    app = _fresh_app(monkeypatch)
    with TestClient(app) as client:
        r = client.get("/admin/ui")
    assert r.status_code == 404


def test_spa_served_when_dist_present(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>admin</title>SPA_ROOT")
    (dist / "assets" / "app.js").write_text("console.log('app');")

    _base_env(monkeypatch)
    monkeypatch.setenv("SERVE_FRONTEND", "true")
    monkeypatch.setenv("FRONTEND_DIST_PATH", str(dist))
    app = _fresh_app(monkeypatch)

    with TestClient(app) as client:
        r_root = client.get("/admin/ui")
        r_deep = client.get("/admin/ui/carts")
        r_asset = client.get("/admin/ui/assets/app.js")
        r_api = client.get("/admin/stats/items")

    assert r_root.status_code == 200 and "SPA_ROOT" in r_root.text
    assert r_deep.status_code == 200 and "SPA_ROOT" in r_deep.text
    assert r_asset.status_code == 200 and "console.log" in r_asset.text
    assert r_api.status_code == 401


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
