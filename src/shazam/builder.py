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
from collections.abc import Iterable
from dataclasses import dataclass

import psycopg

from shazam.audio import AudioLoadError, load
from shazam.config import DspConfig
from shazam.database import SongRecord, copy_fingerprints, existing_paths, insert_song
from shazam.fingerprint import fingerprint_signal
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
    duration: float | None = None
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
    config = DspConfig()
    try:
        signal = load(meta.path, config)
        return TrackResult(
            meta=meta,
            hashes=fingerprint_signal(signal, config),
            duration=len(signal) / config.sample_rate,
        )
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
    track_timeout: float = 120.0,
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
        track_timeout: Seconds to wait for any one track before giving up on
            it. Guards against a worker dying without raising.

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
    #
    # Results are pulled with an explicit timeout rather than a plain for-loop.
    # A worker that *raises* is already handled inside _fingerprint_track, but a
    # worker that dies outright — a segfault in the audio decoder, or the OOM
    # killer — never raises anything, and the pool then waits on a result that
    # will never arrive. Verified: the build delivers every other track and then
    # blocks permanently, with no error and no summary. FMA is known to contain
    # truncated files, so this is a question of when, not whether.
    with multiprocessing.Pool(workers, maxtasksperchild=200) as pool:
        results = pool.imap_unordered(_fingerprint_track, pending)
        for index in range(1, total + 1):
            try:
                result = results.next(timeout=track_timeout)
            except StopIteration:
                break
            except multiprocessing.TimeoutError:
                summary.failed += 1
                print(
                    f"[{index:>5}/{total}] worker lost or exceeded "
                    f"{track_timeout:.0f}s — skipping",
                    flush=True,
                )
                continue

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
        if meta.catalogue_key() in already_built:
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
            # The catalogue key, not a filesystem path. What is stored has to
            # identify the same track from any machine and any mount point,
            # because that uniqueness is what makes a build resumable and what
            # stops a rerun from inserting duplicates. See
            # TrackMeta.catalogue_key for why an absolute path fails at this.
            path=result.meta.catalogue_key(),
            duration=result.duration,
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
    if result.error is not None:
        status = f"FAILED {result.error}"
    elif not result.hashes:
        # Distinguished from a decode failure: the file read fine but produced
        # nothing to store — silence, or audio too quiet to clear the peak floor.
        status = "SKIPPED no fingerprints"
    else:
        status = f"{len(result.hashes):>6} hashes"

    print(
        f"[{index:>5}/{total}] {result.meta.title[:44]:<44} {status}  "
        f"~{remaining / 60:.0f}m left",
        flush=True,
    )
