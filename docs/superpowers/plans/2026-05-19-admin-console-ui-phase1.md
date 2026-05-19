# Admin Console UI — Phase 1 (Backend SPA Serving) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add backend infrastructure so a future React SPA build (`frontend/dist`) can be served at `/admin/ui`, gated by a `serve_frontend` setting, without breaking anything when the frontend doesn't exist yet.

**Architecture:** Two `Settings` fields (`serve_frontend`, `frontend_dist_path`) + a conditional block in `create_app()` that mounts `StaticFiles` for `/admin/ui/assets` and adds an SPA fallback returning `index.html`. The block is double-gated: `serve_frontend=True` AND the dist directory exists. Default `serve_frontend=False` → zero production behavior change.

**Tech Stack:** FastAPI / starlette `StaticFiles` + `FileResponse` (no new dependency), pydantic-settings, pytest.

---

## Working Branch

Working directory: `/Users/takuma/cross/ec/.worktrees/admin-console-ui`
Branch: `feature/admin-console-ui` (off `origin/main`).
Spec: `docs/superpowers/specs/2026-05-19-admin-console-ui-design.md` (Phase 1 scope only).

---

## File Structure

```
app/core/config.py          # modify — add serve_frontend, frontend_dist_path
app/main.py                 # modify — conditional SPA serving in create_app()
tests/test_spa_serving.py   # new — verifies gating + fallback + API coexistence
```

No `frontend/` directory is created (Phase 2, separate task).

---

## Context the implementer needs

`app/core/config.py` currently:

```python
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    database_url: str
    rabbitmq_url: str
    # ... (jwt_*, otel_*, checkout_*, max_consumer_retries) ...


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
```

`get_settings()` is `@lru_cache`d — **tests that change env must call `get_settings.cache_clear()`**.

`app/main.py` `create_app()` currently ends with:

```python
    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(items_router)
    app.include_router(cart_router)
    from app.modules.admin.router import router as admin_router

    app.include_router(admin_router)
    return app


app = create_app()
```

`create_app()` does NOT currently read settings. We add a settings read at the end of `create_app()` for the SPA block.

---

## Task 1: Add `serve_frontend` / `frontend_dist_path` Settings

**Files:**
- Modify: `app/core/config.py`
- Test: `tests/test_spa_serving.py` (create with the first test only)

- [ ] **Step 1: Write the failing test**

Create `tests/test_spa_serving.py`:

```python
from __future__ import annotations

import pytest


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    # Required env so Settings() can instantiate.
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
```

- [ ] **Step 2: Run, verify it fails**

```bash
uv run pytest tests/test_spa_serving.py -v
```

Expected: FAIL — `Settings` has no attribute `serve_frontend`.

- [ ] **Step 3: Add the fields**

Edit `app/core/config.py`. Add after `max_consumer_retries: int = 5`:

```python
    serve_frontend: bool = False
    frontend_dist_path: str = "frontend/dist"
```

- [ ] **Step 4: Run, verify pass**

```bash
uv run pytest tests/test_spa_serving.py -v
```

Expected: 2 PASS.

- [ ] **Step 5: Lint / type**

```bash
uv run ruff check app/core/config.py tests/test_spa_serving.py
uv run ruff format --check app/core/config.py tests/test_spa_serving.py
uv run mypy app
```

- [ ] **Step 6: Commit**

```bash
git add app/core/config.py tests/test_spa_serving.py
git commit -m "feat(config): add serve_frontend / frontend_dist_path settings"
```

---

## Task 2: SPA serving in `create_app()`

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_spa_serving.py` (append integration tests)

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_spa_serving.py`:

```python
from pathlib import Path

from starlette.testclient import TestClient


def _base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    monkeypatch.setenv("RABBITMQ_URL", "amqp://guest:guest@localhost/")
    monkeypatch.setenv("JWT_PRIVATE_KEY_PATH", "/tmp/x.pem")
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", "/tmp/x.pem")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")


def _fresh_app(monkeypatch: pytest.MonkeyPatch):
    # Settings is lru_cached via get_settings(); clear so env changes take effect.
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    return create_app()


def test_spa_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.delenv("SERVE_FRONTEND", raising=False)
    app = _fresh_app(monkeypatch)
    # TestClient with lifespan disabled (we only test routing, not startup).
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
    assert r.status_code == 404  # gracefully not registered


def test_spa_served_when_dist_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>admin</title>SPA_ROOT")
    (dist / "assets" / "app.js").write_text("console.log('app');")

    _base_env(monkeypatch)
    monkeypatch.setenv("SERVE_FRONTEND", "true")
    monkeypatch.setenv("FRONTEND_DIST_PATH", str(dist))
    app = _fresh_app(monkeypatch)

    with TestClient(app) as client:
        # Root SPA path -> index.html
        r_root = client.get("/admin/ui")
        # Client-route deep link -> SPA fallback (still index.html)
        r_deep = client.get("/admin/ui/carts")
        # Static asset -> served file
        r_asset = client.get("/admin/ui/assets/app.js")
        # API route NOT shadowed by SPA fallback (401 because unauthenticated,
        # proving it reached the admin API, not index.html)
        r_api = client.get("/admin/stats/items")

    assert r_root.status_code == 200 and "SPA_ROOT" in r_root.text
    assert r_deep.status_code == 200 and "SPA_ROOT" in r_deep.text
    assert r_asset.status_code == 200 and "console.log" in r_asset.text
    assert r_api.status_code == 401
```

Note: `TestClient(app)` runs lifespan (startup/shutdown). The lifespan opens an MQ connection but already degrades gracefully when RabbitMQ is absent (`structlog warning`, `mq_connection=None`). So `with TestClient(app)` works without Docker. If lifespan still errors for another reason, fall back to `TestClient(app, raise_server_exceptions=True)` and investigate — but the existing `test_app_factory` tests already use the app under TestClient without Docker, so this is a proven pattern.

- [ ] **Step 2: Run, verify failures**

```bash
uv run pytest tests/test_spa_serving.py -v
```

Expected: `test_spa_disabled_by_default` passes (404 is default behavior), the other two new tests fail (no SPA wiring yet — `test_spa_served_when_dist_present` fails because `/admin/ui` is 404).

- [ ] **Step 3: Implement SPA serving in `app/main.py`**

Add imports near the top of `app/main.py` (with the existing imports):

```python
from pathlib import Path

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
```

(Keep existing `from fastapi.responses import JSONResponse` — combine into `from fastapi.responses import FileResponse, JSONResponse` to satisfy ruff's import grouping, or add a separate line; the implementer should match the existing style and re-run ruff.)

In `create_app()`, replace the tail:

```python
    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(items_router)
    app.include_router(cart_router)
    from app.modules.admin.router import router as admin_router

    app.include_router(admin_router)
    return app
```

with:

```python
    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(items_router)
    app.include_router(cart_router)
    from app.modules.admin.router import router as admin_router

    app.include_router(admin_router)

    _mount_spa(app)
    return app


def _mount_spa(app: FastAPI) -> None:
    """Serve the built React SPA at /admin/ui when enabled and present.

    Double-gated: serve_frontend setting AND the dist directory exists.
    Default serve_frontend=False -> no-op (zero production impact until
    Phase 2 ships the frontend and ops sets SERVE_FRONTEND=true).
    """
    settings = get_settings()
    if not settings.serve_frontend:
        return
    dist = Path(settings.frontend_dist_path)
    if not dist.is_dir():
        return

    assets_dir = dist / "assets"
    if assets_dir.is_dir():
        app.mount(
            "/admin/ui/assets",
            StaticFiles(directory=assets_dir),
            name="admin-ui-assets",
        )

    index_file = dist / "index.html"

    @app.get("/admin/ui", include_in_schema=False)
    @app.get("/admin/ui/{rest:path}", include_in_schema=False)
    async def _spa_fallback(rest: str = "") -> FileResponse:
        return FileResponse(index_file)
```

Also add the import for `get_settings`. The file currently imports `from app.core.config import Settings`; change to:

```python
from app.core.config import Settings, get_settings
```

- [ ] **Step 4: Run, verify all pass**

```bash
uv run pytest tests/test_spa_serving.py -v
```

Expected: 5 PASS (2 settings + 3 serving).

- [ ] **Step 5: Run the wider unit suite to ensure no regression**

```bash
uv run pytest -m "not slow"
```

Expected: all green (the `app = create_app()` module-level call now also runs `_mount_spa`, which is a no-op by default).

- [ ] **Step 6: Lint / format / type**

```bash
uv run ruff check app/main.py tests/test_spa_serving.py
uv run ruff format --check app/main.py tests/test_spa_serving.py
uv run mypy app
```

Expected: clean. If mypy complains about the nested route function return type or the stacked decorators, annotate `_spa_fallback` return as `-> FileResponse` (already shown) and ensure `rest: str = ""` is typed.

- [ ] **Step 7: Commit**

```bash
git add app/main.py tests/test_spa_serving.py
git commit -m "feat(main): serve React SPA at /admin/ui when serve_frontend enabled"
```

---

## Task 3: Final verification, push, PR

- [ ] **Step 1: Full check matrix**

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest -m "not slow"
uv run pytest -m slow
```

Expected: ruff/format/mypy clean; unit suite green (incl. 5 new SPA tests); slow suite green (unchanged — no slow tests added).

- [ ] **Step 2: Inspect history**

```bash
git log --oneline origin/main..HEAD
```

Expected: 1 spec commit (already there) + 2 implementation commits = 3 total.

- [ ] **Step 3: Push**

```bash
git push -u origin feature/admin-console-ui
```

If harness denies, ask the user to push.

- [ ] **Step 4: Open PR**

```bash
gh pr create \
  --base main \
  --head feature/admin-console-ui \
  --title "Admin console UI — Phase 1: backend SPA serving infra" \
  --body "$(cat <<'EOF'
## Summary

Phase 1 of `docs/superpowers/specs/2026-05-19-admin-console-ui-design.md`. **Backend only** — the React frontend is a separate task (Phase 2), per the user's request to split implementation.

- `Settings.serve_frontend: bool = False` + `Settings.frontend_dist_path: str = "frontend/dist"`
- `create_app()` mounts the built SPA at `/admin/ui` **only when** `serve_frontend=True` AND the dist directory exists:
  - `/admin/ui/assets/*` → `StaticFiles`
  - `GET /admin/ui` and `/admin/ui/{rest:path}` → `index.html` (SPA fallback for client-side routing)
- Default `serve_frontend=False` → zero production behavior change; safe while `frontend/` does not exist.
- No changes to auth or admin API. `/auth/login`,`/auth/refresh`,`/auth/logout` already exist and are untouched (verified).

## Test plan

- [x] `uv run ruff check .` / `ruff format --check .` / `mypy app` clean
- [x] `uv run pytest -m "not slow"` — 5 new tests: settings defaults/override, SPA disabled-by-default, enabled-but-dist-missing (graceful 404), served-when-present (root + deep link fallback + asset + API-not-shadowed)
- [x] `uv run pytest -m slow` — unchanged, green
- [ ] CI green on PR

## Phase 2 (separate task, NOT in this PR)

`frontend/` React + Vite implementation, CI `frontend` job, dependabot npm. Design recorded in spec §8. Enable in prod later via `SERVE_FRONTEND=true`.

## Follow-ups (spec §9)

httpOnly cookie auth, DLQ redrive/drain UI, pagination, search, CSS framework, i18n, audit-log screen, E2E.
EOF
)"
```

- [ ] **Step 5: Watch CI** — all 7 required checks must be green.

---

## Self-Review Notes

**Spec coverage (Phase 1 only):**
- §2 Phase 1 goals (serve_frontend setting, gated mount, safe-when-absent, API coexistence, tests, no auth/admin change) → Tasks 1 + 2
- §4 architecture (paths, Settings, main.py code) → Task 2 (matches spec's code block)
- §5 file changes (config.py, main.py, test) → matches plan
- §6 edge cases (disabled / dist-missing / present / asset / API-not-shadowed) → Task 2 test cases 1:1
- §7 test strategy (monkeypatch env + cache_clear) → Task 2 `_fresh_app` helper
- §8 Phase 2 → explicitly out of scope, mentioned in PR body
- §9/§10 → follow-ups in PR body

**Placeholder scan:** No "TBD"/"as needed". The import-style note ("combine or separate line, match existing style + re-run ruff") is a concrete instruction, not a placeholder.

**Type consistency:**
- `serve_frontend: bool`, `frontend_dist_path: str` consistent between Task 1 (definition), Task 2 (`_mount_spa` usage), tests.
- `get_settings()` (lru_cached) used in `_mount_spa`; tests call `get_settings.cache_clear()` before `create_app()` — consistent.
- `_spa_fallback(rest: str = "") -> FileResponse` signature consistent with the two stacked route decorators (`/admin/ui` passes default `""`, `/admin/ui/{rest:path}` passes the path).
- SPA base path `/admin/ui` consistent across spec §4, plan Task 2, tests.

**Risk note:** `TestClient(app)` triggers lifespan, which opens an MQ connection. The existing lifespan already degrades gracefully without RabbitMQ (sets `mq_connection=None` + warning) — proven by existing `test_app_factory` tests running without Docker. If a future change makes lifespan hard-fail without MQ, these tests would need `TestClient(app)` lifespan suppression; not expected now.
