"""Pydantic response models for the HTTP API.

Fields are declared in snake_case, the project convention, and exposed to
clients in camelCase via an alias generator — JSON consumers (the React web
client) get the casing they expect without leaking it into the Python side.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    """Base for every response model: camelCase on the wire, snake_case in Python."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class MatchInfo(ApiModel):
    """The recognised track, when the query was found in the corpus."""

    song_id: int
    title: str
    artist: str | None
    score: int
    aligned_fraction: float
    strength: str
    offset_seconds: float


class MatchResponse(ApiModel):
    """Body of ``POST /api/match``. ``match`` is ``None`` on an honest non-match."""

    match: MatchInfo | None
    query_hashes: int
    elapsed_ms: int


class SongSummary(ApiModel):
    """One row of the catalogue listing."""

    id: int
    title: str
    artist: str | None
    duration: float | None


class SongListResponse(ApiModel):
    """Body of ``GET /api/songs``."""

    items: list[SongSummary]
    total: int
    limit: int
    offset: int


class HealthResponse(ApiModel):
    """Body of ``GET /api/health``. Counts are cached at startup, never scanned live."""

    status: str
    songs: int
    fingerprints: int
    database: str
