"""Matching a query against the fingerprint database by offset histogram.

Shared hashes alone prove nothing — popular chords collide across unrelated
tracks. What proves a match is *consistency*: if the query really is an excerpt
of track X starting 47 seconds in, then every shared hash must sit at the same
distance ``db_offset - query_offset``. Unrelated collisions scatter.

    right track:  ▁▁▁▁▁█▁▁▁▁▁   one sharp spike -> the excerpt starts there
    wrong track:  ▂▁▃▂▁▂▂▃▁▂▂   flat -> coincidence

So the tallest spike is the answer and its height is the score.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import psycopg

from shazam.config import DspConfig, MatchConfig
from shazam.database import fetch_song, lookup


@dataclass(frozen=True)
class Candidate:
    """One track's best explanation of the query.

    Attributes:
        song_id: Track that produced the aligned hashes.
        score: How many query hashes agree on ``offset_frames``.
        offset_frames: Where in the track the query appears to begin.
    """

    song_id: int
    score: int
    offset_frames: int


@dataclass(frozen=True)
class MatchResult:
    """A recognised track, with enough context to explain the decision.

    Attributes:
        song_id: Database id of the matched track.
        title: Track title.
        artist: Performer, when the catalogue knows one.
        score: Aligned hash count at the histogram spike.
        confidence: ``score`` over the number of hashes the query produced.
        offset_seconds: Where in the track the recording started.
    """

    song_id: int
    title: str
    artist: str | None
    score: int
    confidence: float
    offset_seconds: float


def rank_candidates(
    query_hashes: Sequence[tuple[int, int]],
    hits: Iterable[tuple[int, int, int]],
) -> list[Candidate]:
    """Score every track that shares hashes with the query.

    Args:
        query_hashes: ``(hash, query_frame)`` pairs from the recording.
        hits: ``(hash, song_id, db_offset)`` rows the database returned for
            those hashes.

    Returns:
        One candidate per track, best first.
    """
    # A hash can repeat within a query at different times, so keep every
    # occurrence: each is an independent vote for its own alignment.
    query_frames: dict[int, list[int]] = {}
    for hash_value, frame in query_hashes:
        query_frames.setdefault(hash_value, []).append(frame)

    votes: Counter[tuple[int, int]] = Counter()
    for hash_value, song_id, db_offset in hits:
        for query_frame in query_frames.get(hash_value, ()):
            votes[(song_id, db_offset - query_frame)] += 1

    best_per_song: dict[int, Candidate] = {}
    for (song_id, offset), score in votes.items():
        current = best_per_song.get(song_id)
        if current is None or score > current.score:
            best_per_song[song_id] = Candidate(song_id, score, offset)

    return sorted(best_per_song.values(), key=lambda c: c.score, reverse=True)


def select_match(
    candidates: Sequence[Candidate],
    config: MatchConfig | None = None,
) -> Candidate | None:
    """Apply both acceptance thresholds, or return ``None``.

    Returning ``None`` is a real answer. A system that always names a track is
    worse than one that admits it does not know, because a confident wrong
    answer is indistinguishable from a right one to whoever is listening.
    """
    config = config or MatchConfig()

    if not candidates:
        return None

    best = candidates[0]
    if best.score < config.min_score:
        return None

    if len(candidates) > 1:
        runner_up = candidates[1]
        # A runner-up with no votes cannot make the winner ambiguous.
        if runner_up.score > 0 and best.score / runner_up.score < config.score_ratio:
            return None

    return best


def offset_to_seconds(offset_frames: int, config: DspConfig | None = None) -> float:
    """Convert a histogram offset into the second it corresponds to in the track."""
    config = config or DspConfig()
    return offset_frames * config.frame_duration


def identify(
    conn: psycopg.Connection,
    query_hashes: Sequence[tuple[int, int]],
    config: DspConfig | None = None,
    match_config: MatchConfig | None = None,
) -> MatchResult | None:
    """Look up a query's hashes and decide which track, if any, it came from.

    The single entry point shared by the CLI and the HTTP API, so both apply
    identical thresholds and cannot drift apart.

    Args:
        conn: Open database connection.
        query_hashes: ``(hash, frame)`` pairs from the recording.
        config: DSP parameters.
        match_config: Acceptance thresholds.

    Returns:
        The matched track, or ``None`` when the evidence is insufficient.
    """
    config = config or DspConfig()

    if not query_hashes:
        return None

    hits = lookup(conn, [hash_value for hash_value, _ in query_hashes])
    chosen = select_match(rank_candidates(query_hashes, hits), match_config)
    if chosen is None:
        return None

    song = fetch_song(conn, chosen.song_id)
    if song is None:
        return None

    title, artist = song
    return MatchResult(
        song_id=chosen.song_id,
        title=title,
        artist=artist,
        score=chosen.score,
        confidence=chosen.score / len(query_hashes),
        offset_seconds=offset_to_seconds(chosen.offset_frames, config),
    )
