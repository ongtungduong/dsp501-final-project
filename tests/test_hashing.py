"""Invariants of peak pairing and hash packing.

The one property everything else rests on: a hash must not depend on where in
the track its peaks occurred. A user records five seconds from the middle of a
song, and those five seconds have to produce the same hashes they produced when
the whole track was fingerprinted. That is why a hash encodes the *difference*
between two peak times and never an absolute time.
"""

from __future__ import annotations

import pytest

from shazam.config import DspConfig
from shazam.hashing import HASH_BITS, generate_hashes, pack_hash, unpack_hash
from shazam.peaks import Peak


def _peaks(*pairs: tuple[int, int]) -> list[Peak]:
    """Build peaks from (frame, freq_bin) pairs."""
    return [Peak(frame=frame, freq_bin=freq_bin) for frame, freq_bin in pairs]


def test_pack_unpack_round_trip() -> None:
    for f1, f2, delta in [(0, 0, 0), (41, 87, 12), (512, 512, 86), (1023, 1023, 4095)]:
        assert unpack_hash(pack_hash(f1, f2, delta)) == (f1, f2, delta)


def test_hash_fits_in_32_bits() -> None:
    """Design decision #4: the widest hash overflows a signed PostgreSQL INTEGER.

    4294967295 > 2147483647, so the column must be BIGINT. A hash this wide
    comes from a high f1 — i.e. a high-frequency peak — so an INTEGER column
    would corrupt only part of the data and only at the top of the spectrum.
    """
    widest = pack_hash(1023, 1023, 4095)

    assert widest == 2**HASH_BITS - 1 == 4294967295
    assert widest > 2147483647, "must not fit in a signed 32-bit INTEGER"


def test_every_reachable_hash_stays_within_32_bits() -> None:
    config = DspConfig()
    max_bin = config.n_bins - 1
    max_delta = config.seconds_to_frames(config.max_time_delta)

    assert pack_hash(max_bin, max_bin, max_delta) < 2**HASH_BITS


def test_hashes_are_invariant_to_time_shift() -> None:
    """The property the whole scheme depends on.

    Shifting every peak later by a constant must leave the hashes untouched and
    move the anchor offsets by exactly that constant.
    """
    config = DspConfig()
    layout = [(10, 100), (15, 200), (20, 150), (25, 300), (30, 250)]
    original = _peaks(*layout)
    shifted = _peaks(*[(frame + 500, freq) for frame, freq in layout])

    from_original = list(generate_hashes(original, config))
    from_shifted = list(generate_hashes(shifted, config))

    assert [h for h, _ in from_original] == [h for h, _ in from_shifted]
    assert [t + 500 for _, t in from_original] == [t for _, t in from_shifted]


def test_pairs_respect_the_target_zone() -> None:
    config = DspConfig()
    min_frames = config.seconds_to_frames(config.min_time_delta)
    max_frames = config.seconds_to_frames(config.max_time_delta)

    # Peaks spread widely enough that some pairs fall outside the zone.
    peaks = _peaks(*[(frame, 100 + frame) for frame in range(0, 400, 3)])

    for hash_value, _ in generate_hashes(peaks, config):
        _, _, delta = unpack_hash(hash_value)
        assert min_frames <= delta <= max_frames


def test_target_zone_boundaries_are_inclusive() -> None:
    """Both edges of the zone must be admitted, not one short of each.

    This is worth pinning precisely: on the live corpus the minimum delta is
    also the *most common* one, so flipping ``<=`` to ``<`` at the near edge
    would silently discard a few percent of every fingerprint in the database
    while every other test still passed.
    """
    config = DspConfig()
    min_delta = config.seconds_to_frames(config.min_time_delta)
    max_delta = config.seconds_to_frames(config.max_time_delta)

    def deltas_for(gap: int) -> list[int]:
        pairs = generate_hashes(_peaks((0, 100), (gap, 200)), config)
        return [unpack_hash(h)[2] for h, _ in pairs]

    assert deltas_for(min_delta) == [min_delta], "near edge must be included"
    assert deltas_for(max_delta) == [max_delta], "far edge must be included"
    assert deltas_for(min_delta - 1) == [], "inside the near edge must be excluded"
    assert deltas_for(max_delta + 1) == [], "beyond the far edge must be excluded"


def test_unsorted_peaks_are_rejected() -> None:
    """Pairing assumes time order; out-of-order input would silently under-hash.

    Peaks arriving unsorted produce negative deltas, which fall below the
    minimum and get skipped — fewer fingerprints, no error, no clue why
    recognition got worse.
    """
    config = DspConfig()

    with pytest.raises(ValueError, match="sorted"):
        list(generate_hashes(_peaks((50, 100), (10, 200), (90, 150)), config))


def test_fan_out_is_respected() -> None:
    """Each anchor pairs with at most fan_out targets — this bounds database size."""
    config = DspConfig(fan_out=3)
    peaks = _peaks(*[(frame, 200) for frame in range(0, 200, 5)])

    counts: dict[int, int] = {}
    for _, anchor_time in generate_hashes(peaks, config):
        counts[anchor_time] = counts.get(anchor_time, 0) + 1

    assert counts, "expected some pairs"
    assert max(counts.values()) <= config.fan_out
    # An anchor with plenty of targets ahead of it must actually use its full
    # allowance — asserting only the ceiling would pass an implementation that
    # emitted a single pair per anchor.
    assert counts[peaks[0].frame] == config.fan_out


def test_anchor_time_is_the_earlier_peak() -> None:
    config = DspConfig()
    peaks = _peaks((10, 100), (20, 200))

    results = list(generate_hashes(peaks, config))

    assert results
    assert all(anchor_time == 10 for _, anchor_time in results)


def test_frequency_order_is_preserved_in_the_hash() -> None:
    """f1 is the anchor's bin and f2 the target's — swapping them is a different hash."""
    config = DspConfig()

    rising = list(generate_hashes(_peaks((0, 100), (20, 300)), config))
    falling = list(generate_hashes(_peaks((0, 300), (20, 100)), config))

    assert rising and falling
    assert rising[0][0] != falling[0][0]


def test_too_few_peaks_yield_no_hashes() -> None:
    config = DspConfig()

    assert list(generate_hashes([], config)) == []
    assert list(generate_hashes(_peaks((5, 100)), config)) == []


@pytest.mark.parametrize("bad", [(-1, 0, 0), (1024, 0, 0), (0, 1024, 0), (0, 0, 4096)])
def test_out_of_range_components_are_rejected(bad: tuple[int, int, int]) -> None:
    """Silent bit overflow would corrupt hashes in a way that is very hard to trace."""
    with pytest.raises(ValueError):
        pack_hash(*bad)
