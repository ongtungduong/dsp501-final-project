"""Building the fingerprint corpus across multiple processes.

Fingerprinting is pure CPU work and each track is independent, so it divides
across cores almost linearly. Database writing does not: parallel writers would
mean contending connections and interleaved transactions for no gain, since the
bottleneck is the FFT, not the insert.

    Pool(n_workers) ──▶ worker: decode -> STFT -> peaks -> hashes
                            │ returns (metadata, hashes)
                            ▼
                  main process: INSERT song, COPY fingerprints

Workers compute, the parent writes. All ten cores stay busy on the expensive
part while transactions stay simple.
"""

from __future__ import annotations

import multiprocessing
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

import psycopg

from shazam.audio import AudioLoadError
from shazam.config import DspConfig
from shazam.database import SongRecord, copy_fingerprints, existing_paths, insert_song
from shazam.fingerprint import fingerprint_file
from shazam.sources import TrackMeta


@dataclass(frozen=True)
class TrackResult:
    """What a worker returns for one track.

    ``error`` being set means the track failed and must be skipped. A worker
    never raises: the Free Music Archive is known to contain truncated files,
    and one bad MP3 must not take down a build of 8000 tracks.
    """

    meta: TrackMeta
    hashes: list[tuple[int, int]]
    error: str | None = None


@dataclass
class BuildSummary:
    """Totals for a completed build."""

    added: int = 0
    skipped: int = 0
    failed: int = 0
    fingerprints: int = 0
    seconds: float = 0.0


def _fingerprint_track(meta: TrackMeta) -> TrackResult:
    """Worker entry point: fingerprint one track, converting failure to data."""
    try:
        return TrackResult(meta=meta, hashes=fingerprint_file(meta.path))
    except AudioLoadError as exc:
        return TrackResult(meta=meta, hashes=[], error=str(exc))
    except Exception as exc:  # a corrupt file can fail in ways libsndfile does not own
        return TrackResult(meta=meta, hashes=[], error=f"{type(exc).__name__}: {exc}")


def build(
    conn: psycopg.Connection,
    tracks: Iterable[TrackMeta],
    workers: int | None = None,
    limit: int | None = None,
    config: DspConfig | None = None,
    progress: bool = True,
) -> BuildSummary:
    """Fingerprint tracks in parallel and load them into the database.

    Args:
        conn: Open connection. Only this process writes to it.
        tracks: Catalogue entries to process.
        workers: Worker processes. Defaults to one per core.
        limit: Stop after this many *new* tracks. Useful for tuning parameters
            against a few hundred tracks before committing to all 8000.
        config: DSP parameters.
        progress: Print a per-track progress line.

    Returns:
        Totals for the run.
    """
    config = config or DspConfig()
    workers = workers or multiprocessing.cpu_count()

    already_built = existing_paths(conn)
    summary = BuildSummary()
    started = time.monotonic()

    pending = _select_pending(tracks, already_built, limit, summary)
    if not pending:
        summary.seconds = time.monotonic() - started
        return summary

    total = len(pending)
    # imap_unordered so a slow track never holds up the ones behind it; the
    # parent writes results in whatever order they finish.
    with multiprocessing.Pool(workers) as pool:
        for index, result in enumerate(pool.imap_unordered(_fingerprint_track, pending), 1):
            _store(conn, result, summary)
            if progress:
                _report(index, total, result, summary, started)

    conn.commit()
    summary.seconds = time.monotonic() - started
    return summary


def _select_pending(
    tracks: Iterable[TrackMeta],
    already_built: set[str],
    limit: int | None,
    summary: BuildSummary,
) -> list[TrackMeta]:
    """Drop tracks already in the database so an interrupted build can resume."""
    pending: list[TrackMeta] = []
    for meta in tracks:
        if str(meta.path) in already_built:
            summary.skipped += 1
            continue
        pending.append(meta)
        if limit is not None and len(pending) >= limit:
            break
    return pending


def _store(conn: psycopg.Connection, result: TrackResult, summary: BuildSummary) -> None:
    """Write one worker result, or record why it was skipped."""
    if result.error is not None or not result.hashes:
        summary.failed += 1
        return

    song_id = insert_song(
        conn,
        SongRecord(
            title=result.meta.title,
            artist=result.meta.artist,
            path=str(result.meta.path),
            duration=None,
            source=result.meta.source,
        ),
    )
    if song_id is None:
        # Another pass caught this path between the scan and now.
        summary.skipped += 1
        return

    summary.fingerprints += copy_fingerprints(conn, song_id, result.hashes)
    summary.added += 1

    # Commit per track so an interrupted build keeps everything finished so far.
    conn.commit()


def _report(
    index: int,
    total: int,
    result: TrackResult,
    summary: BuildSummary,
    started: float,
) -> None:
    """Print one progress line with a running estimate of time left."""
    elapsed = time.monotonic() - started
    remaining = (elapsed / index) * (total - index)
    status = f"{len(result.hashes):>6} hashes" if result.error is None else f"FAILED {result.error}"
    print(
        f"[{index:>5}/{total}] {result.meta.title[:44]:<44} {status}  "
        f"~{remaining / 60:.0f}m left",
        flush=True,
    )


def iter_limited(tracks: Iterable[TrackMeta], limit: int | None) -> Iterator[TrackMeta]:
    """Yield at most ``limit`` tracks, or everything when ``limit`` is None."""
    for index, meta in enumerate(tracks):
        if limit is not None and index >= limit:
            return
        yield meta
