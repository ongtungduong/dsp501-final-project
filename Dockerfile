# Multi-stage so a Python change does not rebuild the web app and vice versa.

# ---------------------------------------------------------------------------
# Stage 1 — build the web client
# ---------------------------------------------------------------------------
FROM node:24-alpine AS web-builder

WORKDIR /build

RUN corepack enable

# Manifest and lockfile first: dependency installation is the slow layer and it
# only needs to re-run when these two files change, not on every source edit.
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY web/ ./
RUN pnpm build

# ---------------------------------------------------------------------------
# Stage 2 — resolve Python dependencies
# ---------------------------------------------------------------------------
FROM python:3.14-slim AS python-deps

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /build
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Dependencies before source, again for layer caching. --no-install-project
# resolves the third-party tree without needing our own package present yet.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ ./src/
RUN uv sync --frozen --no-dev

# ---------------------------------------------------------------------------
# Stage 3 — runtime
# ---------------------------------------------------------------------------
FROM python:3.14-slim

# libsndfile1 is the system library soundfile binds to; python:*-slim does not
# ship it, and without it every decode fails at import time. This is the
# classic slim-image trap and the reason the project needs no ffmpeg.
# postgresql-client provides pg_isready for the entrypoint's readiness wait.
RUN apt-get update \
    && apt-get install --no-install-recommends -y libsndfile1 postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Unprivileged user. UID 1000 matches the usual first human account on Linux
# hosts, which keeps the bind-mounted ./data readable and writable without
# widening its permissions.
RUN useradd --create-home --uid 1000 app

WORKDIR /app

COPY --from=python-deps /opt/venv /opt/venv
COPY --from=web-builder /build/dist /app/web/dist
COPY src/ /app/src/
COPY docker/entrypoint.sh /app/docker/entrypoint.sh

RUN chmod +x /app/docker/entrypoint.sh && chown -R app:app /app

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    STATIC_DIR=/app/web/dist

USER app
EXPOSE 8000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000"]
