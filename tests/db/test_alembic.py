import os
import subprocess


def test_alembic_env_py_is_importable() -> None:
    """Smoke test: alembic can fully load env.py without ImportError.

    Uses `alembic current` rather than `alembic heads`/`alembic history` because
    only commands that call `script.run_env()` actually execute `migrations/env.py`.
    Empirically (alembic 1.14), `heads` and `history` read the scripts directory
    but never load env.py, so they pass even when env.py has broken imports.
    `current` and `check` both run env.py; `current` is the simpler of the two.

    env.py imports from the `app` package and then calls `run_migrations_online()`,
    which attempts a real DB connection. We don't have a DB here, so we expect
    the command to fail — but the failure must be a *connection* failure, not a
    `ModuleNotFoundError`. That distinction is exactly what we want to assert:
    catching the case where `app` isn't installable into alembic subprocesses
    (e.g., missing `[tool.hatch.build.targets.wheel] packages = ["app"]`).
    """
    env = os.environ.copy()
    env.setdefault(
        "DATABASE_URL",
        "postgresql+asyncpg://placeholder:placeholder@localhost/placeholder",
    )
    env.setdefault("RABBITMQ_URL", "amqp://guest:guest@localhost/")
    env.setdefault("JWT_PRIVATE_KEY_PATH", "/tmp/priv.pem")
    env.setdefault("JWT_PUBLIC_KEY_PATH", "/tmp/pub.pem")
    env.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

    out = subprocess.run(
        ["uv", "run", "alembic", "current"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    combined = out.stdout + out.stderr

    # The critical assertion: env.py's `from app...` imports must succeed.
    assert "ModuleNotFoundError" not in combined, (
        f"env.py failed to import its dependencies (likely `app` not installable):\n"
        f"stdout={out.stdout!r}\nstderr={out.stderr!r}"
    )
    assert "No module named 'app'" not in combined, (
        f"env.py couldn't import `app` (pyproject missing wheel package config?):\n"
        f"stdout={out.stdout!r}\nstderr={out.stderr!r}"
    )
    # If the run succeeded, env.py loaded AND the DB was reachable — great.
    # If it failed, the only acceptable cause is a connection/operational error.
    if out.returncode != 0:
        connection_markers = (
            "OperationalError",
            "Connect call failed",
            "could not connect",
            "Connection refused",
            "TargetServerAttributeNotMatched",
        )
        assert any(m in combined for m in connection_markers), (
            f"alembic current failed for an unexpected reason:\n"
            f"stdout={out.stdout!r}\nstderr={out.stderr!r}"
        )
