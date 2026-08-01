"""FastAPI application: lifespan-managed pool, CORS, startup checks, error shell.

Assembly only. Endpoint logic lives in :mod:`server.routes`; DSP logic lives
in :mod:`shazam`.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

import psycopg
import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from server.logging_config import configure_logging
from server.routes import AppDeps, router
from server.settings import get_settings
from shazam.config import DatabaseSettings
from shazam.database import create_pool

logger = structlog.get_logger(__name__)

# Only these two tables are required for the API to serve anything meaningful.
# `songs` alone (schema created, corpus empty) is a valid, if useless, state —
# `shazam build` is a separate, deliberate step from `shazam init-db`.
_REQUIRED_TABLES = ("songs", "fingerprints")


class SchemaMissingError(RuntimeError):
    """The database is reachable but has not been initialised.

    Raised during startup so the process refuses to serve traffic instead of
    returning a confusing 500 on the first real request.
    """


def _verify_schema(conn: psycopg.Connection) -> None:
    """Confirm both tables resolve on this connection's search_path.

    Uses ``to_regclass`` rather than filtering ``information_schema.tables``
    to a hardcoded ``'public'`` schema, so this check resolves tables exactly
    the way every unqualified query elsewhere in this module does — a
    connection with a non-default ``search_path`` cannot pass verification
    and then fail on the first real query.
    """
    with conn.cursor() as cursor:
        missing = []
        for table in _REQUIRED_TABLES:
            cursor.execute("SELECT to_regclass(%s)", (table,))
            row = cursor.fetchone()
            if row is None or row[0] is None:
                missing.append(table)

    if missing:
        raise SchemaMissingError(
            f"Database schema is missing table(s) {missing}. "
            "Run `shazam init-db` to create the schema, then `shazam build` "
            "to fingerprint a corpus before starting the API."
        )


def _count_corpus(conn: psycopg.Connection) -> tuple[int, int]:
    """Count songs and fingerprints once. Never repeat this on a request path.

    ``COUNT(*)`` on `fingerprints` is a full scan — cheap here at ten tracks,
    ruinous at the 48-million-row target corpus if it ran per request.
    """
    with conn.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM songs")
        songs = int((cursor.fetchone() or [0])[0])
        cursor.execute("SELECT COUNT(*) FROM fingerprints")
        fingerprints = int((cursor.fetchone() or [0])[0])
    return songs, fingerprints


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)

    db_settings = DatabaseSettings(database_url=settings.database_url)
    pool = create_pool(db_settings)

    try:
        pool.open(wait=True, timeout=10)
        with pool.connection() as conn:
            _verify_schema(conn)
            songs, fingerprints = _count_corpus(conn)
    except Exception:
        pool.close()
        logger.exception("startup_failed")
        raise

    logger.info("startup_complete", songs=songs, fingerprints=fingerprints)

    app.state.deps = AppDeps(
        pool=pool,
        max_upload_bytes=settings.max_upload_bytes,
        corpus_songs=songs,
        corpus_fingerprints=fingerprints,
    )

    try:
        yield
    finally:
        pool.close()
        logger.info("shutdown_complete")


def create_app() -> FastAPI:
    """Build the ASGI app. Kept as a factory so tests can construct fresh instances."""
    settings = get_settings()
    app = FastAPI(title="Shazam Clone API", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def _bind_request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Attach a request id to every log line emitted while handling this request."""
        request_id = uuid4().hex[:12]
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = int((time.perf_counter() - start) * 1000)

        logger.info(
            "request_completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            elapsed_ms=elapsed_ms,
        )
        return response

    @app.exception_handler(Exception)
    async def _unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        """Never leak a stack trace to a client. Full detail goes to the structured log only."""
        logger.exception("unhandled_exception", path=request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Lỗi máy chủ nội bộ."})

    app.include_router(router)
    return app


app = create_app()
