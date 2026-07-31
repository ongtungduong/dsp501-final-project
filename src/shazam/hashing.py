"""Turning a constellation map into time-shift-invariant hashes.

A user's five-second recording can start anywhere in a track. If a fingerprint
depended on absolute time it would never line up. The way out is to pair each
peak with a few nearby later peaks and hash only the *difference* between their
times, keeping the absolute time separately as the stored value:

    hash  = (f1 << 22) | (f2 << 12) | delta_frames    <- position-independent
    value = (song_id, anchor_frame)                    <- absolute, stored apart

The pair also carries far more information than a single peak: two frequencies
plus their spacing is specific enough that accidental collisions are rare, which
is what keeps the offset histogram in `matcher` clean.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from shazam.config import DspConfig
from shazam.peaks import Peak

# Bit layout. f1 and f2 take 10 bits each (0..1023), which covers the 513 bins a
# 1024-point window produces; the time delta takes the low 12 bits (0..4095),
# far more than the ~86 frames a 2-second target zone can reach.
FREQ_BITS = 10
DELTA_BITS = 12
HASH_BITS = FREQ_BITS * 2 + DELTA_BITS

_FREQ_MAX = (1 << FREQ_BITS) - 1
_DELTA_MAX = (1 << DELTA_BITS) - 1
_ANCHOR_SHIFT = FREQ_BITS + DELTA_BITS
_TARGET_SHIFT = DELTA_BITS


def pack_hash(anchor_bin: int, target_bin: int, delta_frames: int) -> int:
    """Pack a peak pair into a single 32-bit integer.

    Args:
        anchor_bin: Frequency bin of the earlier peak.
        target_bin: Frequency bin of the later peak.
        delta_frames: Frames between them.

    Returns:
        The packed hash, in ``0 .. 2**32 - 1``.

    Raises:
        ValueError: If any component would overflow its field. Checked rather
            than masked: a silently truncated hash produces a fingerprint that
            looks fine and matches nothing, which is close to untraceable.
    """
    if not 0 <= anchor_bin <= _FREQ_MAX:
        raise ValueError(f"anchor_bin {anchor_bin} outside 0..{_FREQ_MAX}")
    if not 0 <= target_bin <= _FREQ_MAX:
        raise ValueError(f"target_bin {target_bin} outside 0..{_FREQ_MAX}")
    if not 0 <= delta_frames <= _DELTA_MAX:
        raise ValueError(f"delta_frames {delta_frames} outside 0..{_DELTA_MAX}")

    return (anchor_bin << _ANCHOR_SHIFT) | (target_bin << _TARGET_SHIFT) | delta_frames


def unpack_hash(value: int) -> tuple[int, int, int]:
    """Invert :func:`pack_hash`, returning ``(anchor_bin, target_bin, delta_frames)``."""
    return (
        (value >> _ANCHOR_SHIFT) & _FREQ_MAX,
        (value >> _TARGET_SHIFT) & _FREQ_MAX,
        value & _DELTA_MAX,
    )


def generate_hashes(
    peaks: Sequence[Peak],
    config: DspConfig | None = None,
) -> Iterator[tuple[int, int]]:
    """Pair peaks within the target zone and yield ``(hash, anchor_frame)``.

    For each peak, look ahead at the peaks that follow it and keep the first
    ``fan_out`` whose time distance falls inside
    ``[min_time_delta, max_time_delta]``. Pairs closer than the minimum are too
    easily disturbed by noise; beyond the maximum, a pair stops being a local
    feature and the database grows for nothing.

    Args:
        peaks: Constellation map sorted by frame, as :func:`~shazam.peaks.find_peaks`
            returns.
        config: DSP parameters. Defaults to :class:`~shazam.config.DspConfig`.

    Yields:
        ``(hash, anchor_frame)`` for every accepted pair.
    """
    config = config or DspConfig()
    min_delta = config.seconds_to_frames(config.min_time_delta)
    max_delta = config.seconds_to_frames(config.max_time_delta)

    for index, anchor in enumerate(peaks):
        paired = 0
        for target in peaks[index + 1 :]:
            delta = target.frame - anchor.frame

            # Peaks are time-ordered, so once past the zone every later peak is
            # too — stop rather than scanning the rest of the track.
            if delta > max_delta:
                break
            if delta < min_delta:
                continue

            yield pack_hash(anchor.freq_bin, target.freq_bin, delta), anchor.frame

            paired += 1
            if paired >= config.fan_out:
                break
