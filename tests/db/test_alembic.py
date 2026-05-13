import os
import subprocess


def test_alembic_heads_runs() -> None:
    """Smoke test: alembic can read scripts from a properly wired config.

    Uses `alembic heads` instead of `alembic check` because `check` requires
    a live database connection to diff schema. `heads` only reads the
    scripts directory and exits cleanly when alembic.ini + env.py + versions/
    are properly wired, which is what this scaffolding task is about.
    """
    env = os.environ.copy()
    env.setdefault("DATABASE_URL", "postgresql+asyncpg://placeholder:placeholder@localhost/placeholder")
    env.setdefault("RABBITMQ_URL", "amqp://guest:guest@localhost/")
    env.setdefault("JWT_PRIVATE_KEY_PATH", "/tmp/priv.pem")
    env.setdefault("JWT_PUBLIC_KEY_PATH", "/tmp/pub.pem")
    env.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

    out = subprocess.run(
        ["uv", "run", "alembic", "heads"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert out.returncode == 0, f"alembic heads failed: stdout={out.stdout!r} stderr={out.stderr!r}"
    assert "FAILED" not in out.stdout
    assert "FAILED" not in out.stderr
