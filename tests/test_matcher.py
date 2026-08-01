"""Invariants of offset-histogram matching and the accept/reject thresholds.

Why a histogram of time offsets: if a query really is an excerpt of track X,
then *every* hash they share must line up at the same distance
``db_offset - query_offset``. Coincidental hash collisions scatter across all
offsets instead. So a sharp spike is the evidence, and its height is the score.

Why two thresholds: taking the tallest spike alone always returns some track,
even for white noise. With 8000 tracks in the corpus that is a real risk, so a
match must clear an absolute score *and* beat the runner-up by a clear margin.
"""

from __future__ import annotations

import random

from shazam.config import MatchConfig
from shazam.matcher import rank_candidates, select_match


def _hits(*rows: tuple[int, int, int]) -> list[tuple[int, int, int]]:
    """Build database hits from (hash, song_id, db_offset) rows."""
    return list(rows)


def test_consistent_offset_produces_a_single_sharp_candidate() -> None:
    """An excerpt taken 100 frames into a track scores at offset 100."""
    query = [(1000 + i, i) for i in range(20)]
    hits = _hits(*[(1000 + i, 7, i + 100) for i in range(20)])

    candidates = rank_candidates(query, hits)

    assert candidates[0].song_id == 7
    assert candidates[0].offset_frames == 100
    assert candidates[0].score == 20


def test_one_repeating_hash_cannot_carry_a_match() -> None:
    """A drone, a loop or a metronome must not be enough on its own.

    Scoring vote occurrences instead of distinct hashes lets a single hash that
    repeats through the query stack twenty votes on one alignment, clear the
    absolute floor, and skip the ratio check entirely for want of a runner-up.
    The score is how many *different* hashes agree.
    """
    query = [(9, 4 * i) for i in range(20)]
    hits = _hits(*[(9, 1, 200 + 4 * i) for i in range(20)])

    candidates = rank_candidates(query, hits)

    assert candidates[0].score == 1
    assert select_match(candidates, MatchConfig(min_score=10, score_ratio=2.0)) is None


def test_repeated_hashes_count_once_per_alignment() -> None:
    """Genuine agreement still counts, it just is not multiplied by repetition."""
    query = [(100, 0), (100, 50), (200, 10), (300, 20)]
    hits = _hits((100, 1, 5), (100, 1, 55), (200, 1, 15), (300, 1, 25))

    candidates = rank_candidates(query, hits)

    # Three distinct hashes line up at offset 5; the repeat of hash 100 adds no
    # extra credit beyond the one it already earned.
    assert candidates[0].offset_frames == 5
    assert candidates[0].score == 3


def test_scattered_offsets_do_not_accumulate() -> None:
    """Coincidental collisions land on different offsets and never build a spike."""
    query = [(2000 + i, i) for i in range(40)]
    rng = random.Random(11)
    hits = _hits(*[(2000 + i, 3, i + rng.randrange(0, 5000)) for i in range(40)])

    candidates = rank_candidates(query, hits)

    assert candidates[0].score <= 2


def test_the_real_track_outranks_incidental_collisions() -> None:
    query = [(3000 + i, i) for i in range(30)]
    aligned = [(3000 + i, 5, i + 42) for i in range(30)]
    noise = [(3000 + i, 9, i * 7 + 13) for i in range(30)]

    candidates = rank_candidates(query, _hits(*aligned, *noise))

    assert candidates[0].song_id == 5
    assert candidates[0].score > candidates[1].score


def test_no_hits_yields_no_candidates() -> None:
    assert rank_candidates([(1, 0)], []) == []


def test_match_is_accepted_when_it_clears_both_thresholds() -> None:
    config = MatchConfig(min_score=10, score_ratio=2.0)
    query = [(4000 + i, i) for i in range(30)]
    strong = [(4000 + i, 1, i + 10) for i in range(30)]
    weak = [(4000 + i, 2, i * 3) for i in range(10)]

    chosen = select_match(rank_candidates(query, _hits(*strong, *weak)), config)

    assert chosen is not None
    assert chosen.song_id == 1


def test_match_is_rejected_below_the_absolute_score_floor() -> None:
    """Honest 'not found' beats a confident guess built on four votes."""
    config = MatchConfig(min_score=10, score_ratio=2.0)
    query = [(5000 + i, i) for i in range(4)]
    hits = _hits(*[(5000 + i, 1, i + 3) for i in range(4)])

    assert select_match(rank_candidates(query, hits), config) is None


def test_match_is_rejected_when_the_runner_up_is_close() -> None:
    """Two tracks explaining the query equally well means we cannot tell them apart."""
    config = MatchConfig(min_score=10, score_ratio=2.0)
    query = [(6000 + i, i) for i in range(40)]
    first = [(6000 + i, 1, i + 5) for i in range(30)]
    second = [(6000 + i, 2, i + 9) for i in range(25)]

    chosen = select_match(rank_candidates(query, _hits(*first, *second)), config)

    assert chosen is None


def test_a_lone_candidate_skips_the_ratio_check() -> None:
    """With nothing to compare against, the absolute score has to carry the decision."""
    config = MatchConfig(min_score=10, score_ratio=2.0)
    query = [(7000 + i, i) for i in range(20)]
    hits = _hits(*[(7000 + i, 1, i + 77) for i in range(20)])

    chosen = select_match(rank_candidates(query, hits), config)

    assert chosen is not None
    assert chosen.song_id == 1


def test_white_noise_query_is_rejected() -> None:
    """The scenario the dual threshold exists for: nothing must come back."""
    config = MatchConfig(min_score=10, score_ratio=2.0)
    rng = random.Random(5)
    query = [(rng.randrange(0, 2**32), i) for i in range(200)]
    hits = _hits(
        *[(h, rng.randrange(1, 8000), rng.randrange(0, 1200)) for h, _ in query[:60]]
    )

    assert select_match(rank_candidates(query, hits), config) is None


def test_empty_candidate_list_is_no_match() -> None:
    assert select_match([], MatchConfig()) is None
