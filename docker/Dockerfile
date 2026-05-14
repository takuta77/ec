FROM python:3.14-slim AS builder
ENV PYTHONUNBUFFERED=1
ENV UV_LINK_MODE=copy
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates build-essential \
 && rm -rf /var/lib/apt/lists/*
ADD https://astral.sh/uv/install.sh /tmp/uv-install.sh
RUN sh /tmp/uv-install.sh && cp /root/.local/bin/uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock /app/
RUN uv sync --frozen --no-install-project --no-dev
COPY app /app/app
COPY migrations /app/migrations
COPY alembic.ini /app/alembic.ini

FROM python:3.14-slim AS runtime
ENV PYTHONUNBUFFERED=1
ENV PATH="/app/.venv/bin:${PATH}"
RUN useradd -m app
WORKDIR /app
COPY --from=builder /app /app
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
