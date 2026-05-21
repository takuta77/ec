import pytest

from app.core.config import Settings


def test_settings_loads_required_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:y@localhost/ec")
    monkeypatch.setenv("RABBITMQ_URL", "amqp://guest:guest@localhost/")
    monkeypatch.setenv("JWT_PRIVATE_KEY", "dummy-priv-pem")
    monkeypatch.setenv("JWT_PUBLIC_KEY", "dummy-pub-pem")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")

    s = Settings()
    assert s.database_url.startswith("postgresql+asyncpg://")
    assert s.jwt_algorithm == "RS256"
    assert s.jwt_access_ttl_min == 15
    assert s.jwt_refresh_ttl_days == 14
    assert s.checkout_sweep_interval_sec == 300
    assert s.checkout_timeout_hours == 24
    assert s.max_consumer_retries == 5
