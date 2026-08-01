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
from fastapi.staticfiles import StaticFiles

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
    async def _reject_oversized_bodies(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Refuse a too-large upload before its body is read.

        Checking inside the endpoint is too late to be worth much: FastAPI
        resolves an ``UploadFile`` by parsing the whole multipart body first,
        which streams the entire payload to a temporary file before the handler
        runs. Measured on this code, a 50 MB upload against a 10 MB ceiling was
        accepted in full — nine seconds of bandwidth and 50 MB of temp disk —
        and only then answered 413.

        The declared length is not trustworthy on its own, and a chunked request
        has none at all, so the endpoint keeps its own check as the backstop.
        This just stops the honest, common case cheaply.
        """
        declared = request.headers.get("content-length")
        too_large = (
            declared is not None
            and declared.isdigit()
            and int(declared) > settings.max_upload_bytes
        )
        if too_large:
            limit_mb = settings.max_upload_bytes / (1024 * 1024)
            return JSONResponse(
                status_code=413,
                content={"detail": f"Tệp quá lớn, tối đa {limit_mb:.0f} MB."},
            )
        return await call_next(request)

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

    # Mounted last, and only when configured. A StaticFiles mount at "/" matches
    # every path, so registering it before the router would swallow /api/* and
    # return index.html for API calls. In the packaged image this serves the
    # built web app, which is why the deployment needs no second service and no
    # CORS configuration; in development Vite serves the client instead and this
    # stays unset.
    if settings.static_dir is not None:
        if not settings.static_dir.is_dir():
            raise RuntimeError(
                f"STATIC_DIR points at {settings.static_dir}, which does not exist. "
                "Build the web app first, or leave STATIC_DIR unset to run API-only."
            )
        app.mount(
            "/",
            StaticFiles(directory=settings.static_dir, html=True),
            name="web",
        )

    return app


app = create_app()
