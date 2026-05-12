# EC API

See `docs/superpowers/specs/2026-05-12-ec-api-design.md` for the design.

## Quick start

```bash
uv sync
cp .env.example .env
docker compose up -d --build
docker compose exec api alembic upgrade head
open http://localhost:8000/docs
```
