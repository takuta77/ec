.PHONY: install lint test test-slow up migrate down

install:
	uv sync

lint:
	uv run ruff check . && uv run mypy app

test:
	uv run pytest -m "not slow" -v

test-slow:
	uv run pytest -m slow -v

up:
	docker compose up -d --build

migrate:
	docker compose exec api alembic upgrade head

down:
	docker compose down
