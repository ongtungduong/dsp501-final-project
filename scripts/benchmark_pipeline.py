"""Time each stage of the pipeline so optimisation targets measurement, not guesswork.

Usage:
    uv run python scripts/benchmark_pipeline.py data/songs/00-a-minor.wav
    uv run python scripts/benchmark_pipeline.py song.wav --repeat 5 --no-db
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from shazam.audio import load
from shazam.config import DspConfig
from shazam.database import connect, lookup
from shazam.hashing import generate_hashes
from shazam.peaks import find_peaks
from shazam.stft import stft


def _time(label: str, repeat: int, work: Callable[[], Any]) -> tuple[str, float, Any]:
    """Run ``work`` ``repeat`` times and return the best wall-clock time in ms.

    Best rather than mean: the fastest run is the one least disturbed by other
    activity on the machine, so it is the more stable basis for comparison.
    """
    best = float("inf")
    result = None
    for _ in range(repeat):
        started = time.perf_counter()
        result = work()
        best = min(best, time.perf_counter() - started)
    return label, best * 1000.0, result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--no-db", action="store_true", help="Skip the database lookup stage")
    args = parser.parse_args()

    config = DspConfig()
    rows: list[tuple[str, float]] = []

    label, ms, signal = _time("load + resample", args.repeat, lambda: load(args.audio, config))
    rows.append((label, ms))
    duration = len(signal) / config.sample_rate

    label, ms, spectrogram = _time("STFT", args.repeat, lambda: stft(signal, config).magnitude)
    rows.append((label, ms))

    label, ms, peaks = _time("peak picking", args.repeat, lambda: find_peaks(spectrogram, config))
    rows.append((label, ms))

    label, ms, hashes = _time(
        "hashing", args.repeat, lambda: list(generate_hashes(peaks, config))
    )
    rows.append((label, ms))

    if not args.no_db:
        with connect() as conn:
            label, ms, hits = _time(
                "database lookup",
                args.repeat,
                lambda: lookup(conn, [h for h, _ in hashes]),
            )
        rows.append((label, ms))
        print(f"lookup returned {len(hits):,} rows")

    total = sum(ms for _, ms in rows)

    print(f"\n{args.audio.name} — {duration:.1f}s of audio, best of {args.repeat}\n")
    print(f"{'stage':<20} {'ms':>10} {'% total':>9} {'x realtime':>12}")
    print("-" * 54)
    for label, ms in rows:
        share = 100.0 * ms / total if total else 0.0
        print(f"{label:<20} {ms:>10.1f} {share:>8.1f}% {duration * 1000 / ms:>11.0f}x")
    print("-" * 54)
    print(f"{'total':<20} {total:>10.1f} {100.0:>8.1f}% {duration * 1000 / total:>11.0f}x")
    print(f"\n{len(peaks):,} peaks ({len(peaks) / duration:.1f}/s), {len(hashes):,} hashes")


if __name__ == "__main__":
    main()
