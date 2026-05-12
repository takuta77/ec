from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    database_url: str
    rabbitmq_url: str

    jwt_algorithm: str = "RS256"
    jwt_private_key_path: Path
    jwt_public_key_path: Path
    jwt_access_ttl_min: int = 15
    jwt_refresh_ttl_days: int = 14

    otel_exporter_otlp_endpoint: str
    otel_exporter_otlp_protocol: str = "grpc"
    otel_service_name: str = "ec-api"
    otel_resource_attributes: str = "service.namespace=ec,deployment.environment=local"

    checkout_sweep_interval_sec: int = 300
    checkout_timeout_hours: int = 24
    max_consumer_retries: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()
