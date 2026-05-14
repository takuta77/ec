import pytest

from app.core.telemetry import build_resource


def test_resource_has_service_name(monkeypatch):
    monkeypatch.setenv("OTEL_SERVICE_NAME", "ec-test")
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "service.namespace=ec,deployment.environment=local")
    r = build_resource()
    attrs = dict(r.attributes)
    assert attrs["service.name"] == "ec-test"
    assert attrs["service.namespace"] == "ec"
