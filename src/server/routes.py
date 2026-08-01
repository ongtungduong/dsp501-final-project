"""The four HTTP endpoints.

A thin shell over :func:`shazam.matcher.identify` and :func:`shazam.audio.load_bytes`
— no new DSP logic lives here. Every route reads what it needs off
``request.app.state.deps``, populated once by the app's lifespan handler.
"""

from __future__ import annotations

import io
import time
from dataclasses import dataclass
from typing import Annotated

import matplotlib

matplotlib.use("Agg")  # Must be set before pyplot is imported; no display in a server process.

import matplotlib.pyplot as plt
import numpy as np
import psycopg
import structlog
from fastapi import APIRouter, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from numpy.typing import NDArray
from psycopg_pool import ConnectionPool

from server.schemas import (
    HealthResponse,
    MatchInfo,
    MatchResponse,
    SongListResponse,
    SongSummary,
)
from shazam.audio import AudioLoadError, load_bytes
from shazam.config import DspConfig, MatchConfig
from shazam.fingerprint import fingerprint_signal
from shazam.matcher import MatchResult, identify
from shazam.peaks import find_peaks
from shazam.stft import stft

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api")

# One shared, immutable configuration for every request. The corpus was built
# with these same defaults (design decision #1 requires it); nothing here
# constructs a different one.
_DSP_CONFIG = DspConfig()
_MATCH_CONFIG = MatchConfig()

# Below this, a match decision is not meaningful even if decoding succeeded —
# a handful of STFT frames cannot produce enough hashes to clear `min_score`.
_MIN_QUERY_SECONDS = 1.0


@dataclass(frozen=True)
class AppDeps:
    """Everything a route needs from process startup, bundled onto ``app.state``.

    Attributes:
        pool: Open connection pool. Never closed or reopened per request.
        max_upload_bytes: Ceiling enforced before a body is fully buffered.
        corpus_songs: Track count, counted once at startup.
        corpus_fingerprints: Fingerprint row count, counted once at startup.
    """

    pool: ConnectionPool
    max_upload_bytes: int
    corpus_songs: int
    corpus_fingerprints: int


def _deps(request: Request) -> AppDeps:
    deps: AppDeps = request.app.state.deps
    return deps


def _audio_error_message(exc: AudioLoadError) -> str:
    """Translate a decode failure into a short, honest Vietnamese message.

    Never repeats the exception text verbatim: libsndfile's underlying
    errors are meant for developers, not end users, and the two cases users
    actually hit — corrupt/unsupported audio and audio too short to
    fingerprint — have clear, distinct explanations.
    """
    if "too short" in str(exc).lower():
        return f"Đoạn audio quá ngắn, cần ít nhất {_MIN_QUERY_SECONDS:.0f} giây"
    return "Không đọc được tệp âm thanh, tệp có thể bị hỏng hoặc sai định dạng"


async def _read_upload(file: UploadFile, max_bytes: int) -> bytes:
    """Buffer an upload in memory, refusing early once it is clearly too large.

    Reads at most ``max_bytes + 1``: enough to detect an oversized upload
    without paying to buffer an arbitrarily large body first.
    """
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Tệp quá lớn, giới hạn {max_bytes // (1024 * 1024)} MB",
        )
    if not data:
        raise HTTPException(status_code=422, detail="Tệp tải lên không có dữ liệu")
    return data


async def _decode_audio(data: bytes) -> NDArray[np.float32]:
    """Decode an upload through the canonical signal path and enforce the length floor.

    Raises:
        HTTPException: 422, with a Vietnamese message, if the audio cannot be
            decoded or falls short of :data:`_MIN_QUERY_SECONDS`.
    """
    try:
        signal = await run_in_threadpool(load_bytes, data, _DSP_CONFIG)
    except AudioLoadError as exc:
        logger.info("audio_decode_rejected", reason=str(exc))
        raise HTTPException(status_code=422, detail=_audio_error_message(exc)) from exc

    duration_seconds = signal.shape[0] / _DSP_CONFIG.sample_rate
    if duration_seconds < _MIN_QUERY_SECONDS:
        raise HTTPException(
            status_code=422,
            detail=f"Đoạn audio quá ngắn, cần ít nhất {_MIN_QUERY_SECONDS:.0f} giây",
        )
    return signal


def _identify_signal(
    pool: ConnectionPool, signal: NDArray[np.float32]
) -> tuple[MatchResult | None, int]:
    """Fingerprint and match a decoded signal. Runs in a worker thread.

    Bundles both the CPU-bound fingerprinting and the blocking database
    round trip into one call so the caller only has to offload once.
    """
    hashes = fingerprint_signal(signal, _DSP_CONFIG)
    with pool.connection() as conn:
        result = identify(conn, hashes, _DSP_CONFIG, _MATCH_CONFIG)
    return result, len(hashes)


def _to_match_info(result: MatchResult) -> MatchInfo:
    return MatchInfo(
        song_id=result.song_id,
        title=result.title,
        artist=result.artist,
        score=result.score,
        aligned_fraction=result.aligned_fraction,
        strength=result.strength,
        offset_seconds=result.offset_seconds,
    )


@router.post("/match", response_model=MatchResponse)
async def match_audio(request: Request, file: UploadFile) -> MatchResponse:
    """Identify a recording. Returns ``{"match": null}`` (HTTP 200) when unmatched.

    422 covers audio that cannot be decoded or is too short; a valid query
    that simply is not in the corpus is not an error.
    """
    deps = _deps(request)
    data = await _read_upload(file, deps.max_upload_bytes)
    signal = await _decode_audio(data)

    start = time.perf_counter()
    result, query_hash_count = await run_in_threadpool(_identify_signal, deps.pool, signal)
    elapsed_ms = int((time.perf_counter() - start) * 1000)

    logger.info(
        "match_request",
        query_hashes=query_hash_count,
        score=result.score if result else 0,
        elapsed_ms=elapsed_ms,
        matched=result is not None,
    )

    return MatchResponse(
        match=_to_match_info(result) if result else None,
        query_hashes=query_hash_count,
        elapsed_ms=elapsed_ms,
    )


@router.get("/songs", response_model=SongListResponse)
def list_songs(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str, Query(max_length=200)] = "",
) -> SongListResponse:
    """List the catalogue, paginated and optionally filtered by title.

    A plain ``def`` route: FastAPI runs synchronous handlers in its own
    threadpool automatically, which is exactly where a blocking psycopg call
    belongs.
    """
    deps = _deps(request)
    pattern = f"%{q}%" if q else "%"

    with deps.pool.connection() as conn, conn.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM songs WHERE title ILIKE %s", (pattern,))
        total = int((cursor.fetchone() or [0])[0])

        cursor.execute(
            "SELECT id, title, artist, duration FROM songs "
            "WHERE title ILIKE %s ORDER BY id LIMIT %s OFFSET %s",
            (pattern, limit, offset),
        )
        rows = cursor.fetchall()

    items = [
        SongSummary(id=row[0], title=row[1], artist=row[2], duration=row[3]) for row in rows
    ]
    return SongListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    """Report cached corpus counts plus a cheap, live pool check.

    Never runs ``COUNT(*)`` on the fingerprints table here — that is a full
    scan of tens of millions of rows at full corpus size. Counts come from
    :data:`AppDeps`, taken once at startup.
    """
    deps = _deps(request)
    try:
        with deps.pool.connection(timeout=2) as conn:
            conn.execute("SELECT 1")
        database_status = "connected"
    except psycopg.OperationalError:
        logger.warning("health_check_database_unreachable")
        database_status = "unreachable"

    return HealthResponse(
        status="ok" if database_status == "connected" else "degraded",
        songs=deps.corpus_songs,
        fingerprints=deps.corpus_fingerprints,
        database=database_status,
    )


def _render_spectrogram(signal: NDArray[np.float32]) -> bytes:
    """Draw the STFT spectrogram with the constellation overlaid, as a PNG.

    Exists to let a human confirm the server decoded the signal the way it
    thinks it did — the same reason the matcher pipeline is deterministic and
    shared, made visible.
    """
    result = stft(signal, _DSP_CONFIG)
    peaks = find_peaks(result.magnitude, _DSP_CONFIG)
    spectrum_db = 20.0 * np.log10(result.magnitude + 1e-10)

    fig, ax = plt.subplots(figsize=(10, 4), dpi=100)
    duration = float(result.times[-1]) if result.times.size else 0.0
    nyquist = float(result.freqs[-1]) if result.freqs.size else 0.0
    ax.imshow(
        spectrum_db,
        origin="lower",
        aspect="auto",
        extent=(0.0, duration, 0.0, nyquist),
        cmap="magma",
    )
    if peaks:
        times = [float(result.times[peak.frame]) for peak in peaks]
        freqs = [float(result.freqs[peak.freq_bin]) for peak in peaks]
        ax.scatter(times, freqs, s=6, c="cyan", marker="x", linewidths=0.8)

    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Frequency (Hz)")
    ax.set_title("Spectrogram + constellation")
    fig.tight_layout()

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png")
    plt.close(fig)
    return buffer.getvalue()


@router.post("/spectrogram")
async def spectrogram(request: Request, file: UploadFile) -> Response:
    """Render the query's spectrogram and constellation map as a PNG.

    ``POST``, not ``GET``: this takes a multipart file body, and the Fetch
    API a browser client uses cannot attach a body to a ``GET`` request. Same
    reasoning as ``/api/match``.
    """
    deps = _deps(request)
    data = await _read_upload(file, deps.max_upload_bytes)
    signal = await _decode_audio(data)

    png_bytes = await run_in_threadpool(_render_spectrogram, signal)
    return Response(content=png_bytes, media_type="image/png")
