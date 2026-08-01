"""Command-line interface.

    shazam init-db                                    create the schema
    shazam fetch [--source fma] [--dest data/fma]     download a catalogue
    shazam build [--source {local,fma,all}] [--songs-dir DIR] [--fma-dir DIR]
                 [--limit N] [--workers 10]
    shazam create-index                               after building
    shazam match <file>                               identify an audio file
    shazam listen [--seconds 8]                       identify from the microphone
    shazam stats                                      corpus size and hash frequencies
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator
from pathlib import Path

from shazam import database
from shazam.audio import AudioLoadError, load
from shazam.builder import build
from shazam.config import DspConfig, MatchConfig
from shazam.database import connect
from shazam.fingerprint import fingerprint_signal
from shazam.matcher import MatchResult, identify
from shazam.sources import MusicSource, TrackMeta
from shazam.sources.fma import FmaSource, FmaSourceError
from shazam.sources.local import LocalSource

DEFAULT_SONGS_DIR = Path("data/songs")
DEFAULT_FMA_DIR = Path("data/fma")

# Keyed by the --source flag so `build` and `fetch` share one source registry
# and adding a catalogue never means touching more than this line and a class.
SOURCES: dict[str, MusicSource] = {"local": LocalSource(), "fma": FmaSource()}


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch. Returns a process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    handlers = {
        "init-db": _cmd_init_db,
        "fetch": _cmd_fetch,
        "build": _cmd_build,
        "create-index": _cmd_create_index,
        "match": _cmd_match,
        "listen": _cmd_listen,
        "stats": _cmd_stats,
    }
    return handlers[args.command](args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="shazam", description=__doc__)
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init-db", help="Create the database schema")
    sub.add_parser("create-index", help="Build the hash index after loading")

    fetch_cmd = sub.add_parser("fetch", help="Download a music catalogue")
    fetch_cmd.add_argument(
        "--source", choices=sorted(SOURCES), default="fma", help="Catalogue to download"
    )
    fetch_cmd.add_argument(
        "--dest", type=Path, default=DEFAULT_FMA_DIR, help="Where to store the catalogue"
    )

    build_cmd = sub.add_parser("build", help="Fingerprint tracks into the database")
    build_cmd.add_argument(
        "--source",
        choices=["local", "fma", "all"],
        default="all",
        help="Catalogue to fingerprint from",
    )
    build_cmd.add_argument("--songs-dir", type=Path, default=DEFAULT_SONGS_DIR)
    build_cmd.add_argument("--fma-dir", type=Path, default=DEFAULT_FMA_DIR)
    build_cmd.add_argument("--limit", type=int, default=None, help="Stop after N new tracks")
    build_cmd.add_argument("--workers", type=int, default=None, help="Worker processes")

    match_cmd = sub.add_parser("match", help="Identify an audio file")
    match_cmd.add_argument("audio", type=Path)

    listen_cmd = sub.add_parser("listen", help="Identify from the microphone")
    listen_cmd.add_argument("--seconds", type=float, default=8.0)

    stats_cmd = sub.add_parser("stats", help="Corpus size and hash frequency distribution")
    stats_cmd.add_argument("--top", type=int, default=10)

    return parser


def _cmd_init_db(_: argparse.Namespace) -> int:
    with connect() as conn:
        database.init_schema(conn)
    print("Schema ready. Column `hash` is BIGINT.")
    return 0


def _cmd_create_index(_: argparse.Namespace) -> int:
    print("Building hash index — this takes a few minutes on a full corpus...")
    with connect() as conn:
        database.create_index(conn)
    print("Index built.")
    return 0


def _cmd_fetch(args: argparse.Namespace) -> int:
    source = SOURCES[args.source]
    print(f"Fetching {source.name} into {args.dest}/...")
    try:
        source.fetch(args.dest)
    except FmaSourceError as exc:
        print(f"Download failed: {exc}", file=sys.stderr)
        return 1

    print(f"Done. Run `shazam build --source {args.source}` to fingerprint it.")
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    tracks = list(_collect_tracks(args))
    if not tracks:
        print(f"No audio found. Put audio files in {args.songs_dir}/ and run this again.")
        return 1

    print(f"Found {len(tracks)} tracks. Fingerprinting...")
    with connect() as conn:
        summary = build(conn, tracks, workers=args.workers, limit=args.limit)

    print(
        f"\nAdded {summary.added} tracks, {summary.fingerprints:,} fingerprints "
        f"in {summary.seconds:.1f}s "
        f"(skipped {summary.skipped} already present, {summary.failed} failed)"
    )
    if summary.added:
        print("Run `shazam create-index` before matching.")
    return 0


def _collect_tracks(args: argparse.Namespace) -> Iterator[TrackMeta]:
    """Yield tracks from whichever catalogue ``--source`` selects."""
    roots = {"local": args.songs_dir, "fma": args.fma_dir}
    names = SOURCES if args.source == "all" else {args.source: SOURCES[args.source]}
    for name, source in names.items():
        yield from source.tracks(roots[name])


def _cmd_match(args: argparse.Namespace) -> int:
    config = DspConfig()
    try:
        signal = load(args.audio, config)
    except AudioLoadError as exc:
        print(f"Cannot read {args.audio}: {exc}", file=sys.stderr)
        return 1

    hashes = fingerprint_signal(signal, config)
    print(f"{len(hashes):,} hashes from {len(signal) / config.sample_rate:.1f}s of audio")

    with connect() as conn:
        result = identify(conn, hashes, config, MatchConfig())

    _print_result(result)
    return 0 if result else 2


def _cmd_listen(args: argparse.Namespace) -> int:
    from shazam.recorder import record

    config = DspConfig()
    print(f"Recording {args.seconds:.0f}s — play the music now...")
    try:
        signal = record(args.seconds, config)
    except RuntimeError as exc:
        print(f"Cannot record: {exc}", file=sys.stderr)
        return 1

    hashes = fingerprint_signal(signal, config)
    print(f"{len(hashes):,} hashes captured. Searching...")

    with connect() as conn:
        result = identify(conn, hashes, config, MatchConfig())

    _print_result(result)
    return 0 if result else 2


def _print_result(result: MatchResult | None) -> None:
    """Report the outcome, including an honest failure."""
    if result is None:
        print("\nNot found — this track is not in the corpus.")
        print("If you expected a match: move closer to the speaker and record for longer.")
        return

    artist = result.artist or "unknown artist"
    print(f"\n  {result.title}")
    print(f"  {artist}")
    print(f"  {result.strength} match — {result.score} hashes agree "
          f"({result.aligned_fraction:.1%} of the query aligned)")
    print(f"  the recording starts {result.offset_seconds:.1f}s into the track")


def _cmd_stats(args: argparse.Namespace) -> int:
    with connect() as conn:
        summary = database.stats(conn, top=args.top)

    print(f"Tracks:            {summary.songs:,}")
    print(f"Fingerprints:      {summary.fingerprints:,}")
    print(f"Distinct hashes:   {summary.distinct_hashes:,}")
    if summary.fingerprints and summary.distinct_hashes:
        print(f"Average per hash:  {summary.fingerprints / summary.distinct_hashes:.2f}")

    if summary.most_common:
        # A long tail here means some hashes are shared so widely that they cost
        # lookup time without helping tell tracks apart, and should be pruned.
        print("\nMost frequent hashes:")
        for hash_value, count in summary.most_common:
            print(f"  {hash_value:>12}  {count:>8,} occurrences")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
