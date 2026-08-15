"""Runtime configuration for the HTTP API, validated eagerly.

Phase 7 runs this server in a container with no local config file to fall
back on, so a missing or malformed environment variable must fail loudly at
import time — never surface later as a mysterious connection error on the
first request.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven configuration, read once per process.

    Attributes:
        database_url: libpq connection string for the fingerprint database.
            Deliberately has no default — the CLI's :class:`shazam.config.DatabaseSettings`
            defaults to the local docker-compose instance because it is a dev
            tool; the server requires an explicit value because Phase 7 runs
            it in a container where "local" is not a meaningful default.
        cors_origins: Origins allowed to call this API from a browser.
        max_upload_bytes: Upload size ceiling, rejected before the body is
            fully buffered.
        static_dir: Directory of the built web app to serve. Unset in
            development, where Vite serves it instead; Phase 7 sets it.
        log_level: Standard library level name, e.g. ``"INFO"``, ``"DEBUG"``.
        save_uploads: Opt-in. When true, every audio file POSTed to
            ``/api/match`` is written to ``uploads_dir`` verbatim for audit
            and replay. Off by default so a long-running server cannot quietly
            fill a disk.
        uploads_dir: Directory writes land in when ``save_uploads`` is true.
            Created on startup if it does not exist. Default ``data/uploads``
            is already in ``.gitignore``.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    cors_origins: list[str] = ["http://localhost:5173"]
    max_upload_bytes: int = 10 * 1024 * 1024
    static_dir: Path | None = None
    log_level: str = "INFO"
    save_uploads: bool = False
    uploads_dir: Path = Path("data/uploads")


@lru_cache
def get_settings() -> Settings:
    """Build (once) and return the process-wide settings.

    Raises:
        pydantic.ValidationError: If a required variable such as
            ``DATABASE_URL`` is missing. Not caught here: a server that
            cannot know its own database has no correct thing to do, so it
            should refuse to start rather than start and fail on the first
            request.
    """
    # pydantic-settings populates required fields (`database_url`) from the
    # environment at runtime; mypy cannot see that without the `pydantic.mypy`
    # plugin, which is not registered in the shared, off-limits pyproject.toml.
    # A missing variable still raises ValidationError at this call, exactly as
    # documented above — this silences a false positive, not a real gap.
    return Settings()  # type: ignore[call-arg]
