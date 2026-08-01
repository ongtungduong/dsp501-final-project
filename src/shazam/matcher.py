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

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import psycopg

from shazam.config import DspConfig, MatchConfig
from shazam.database import fetch_song, lookup_histogram


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
        score: How many distinct hashes agree at the histogram spike. This is
            the number the decision is actually made on.
        aligned_fraction: ``score`` over the number of hashes the query
            produced. Deliberately *not* called confidence: most query hashes
            have no counterpart by construction, so even a flawless excerpt of
            a corpus track measures around 0.18, and a microphone recording in
            a room lands nearer 0.02. Presenting it as a percentage would read
            as "18% sure" for a perfect match.
        strength: Coarse label derived from ``score``, for interfaces that need
            something honest to show a person.
        offset_seconds: Where in the track the recording started.
    """

    song_id: int
    title: str
    artist: str | None
    score: int
    aligned_fraction: float
    strength: str
    offset_seconds: float


# Score bands for display. A match must already have cleared min_score and the
# runner-up ratio to exist at all, so even "weak" means accepted — the label
# separates a comfortable identification from a marginal one, and gives the
# interface a reason to suggest recording again.
STRONG_SCORE = 100
MODERATE_SCORE = 30


def match_strength(score: int) -> str:
    """Describe how firmly a match is established."""
    if score >= STRONG_SCORE:
        return "strong"
    if score >= MODERATE_SCORE:
        return "moderate"
    return "weak"


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

    # Distinct hashes per alignment, not vote count. A hash that repeats through
    # the query — a sustained tone, a loop, a metronome — would otherwise stack
    # votes on one alignment by itself and clear the threshold alone, matching
    # any track that shares that single repeating pattern.
    agreeing: dict[tuple[int, int], set[int]] = {}
    for hash_value, song_id, db_offset in hits:
        for query_frame in query_frames.get(hash_value, ()):
            agreeing.setdefault((song_id, db_offset - query_frame), set()).add(hash_value)

    best_per_song: dict[int, Candidate] = {}
    for (song_id, offset), hashes in agreeing.items():
        score = len(hashes)
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

    if len(candidates) > 1 and best.score / candidates[1].score < config.score_ratio:
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

    # Aggregated server-side: see database.lookup_histogram for why the rows
    # are not pulled back and counted here.
    candidates = [
        Candidate(song_id=song_id, score=score, offset_frames=delta)
        for song_id, delta, score in lookup_histogram(conn, query_hashes)
    ]

    chosen = select_match(candidates, match_config)
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
        aligned_fraction=chosen.score / len(query_hashes),
        strength=match_strength(chosen.score),
        offset_seconds=offset_to_seconds(chosen.offset_frames, config),
    )
