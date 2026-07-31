"""The pipeline from a signal to its fingerprints, in one place.

Every caller — the corpus builder, the CLI, the microphone, the HTTP endpoint —
goes through here, so none of them can accidentally assemble the stages in a
different order or with different parameters.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import numpy.typing as npt

from shazam.audio import load
from shazam.config import DspConfig
from shazam.hashing import generate_hashes
from shazam.peaks import find_peaks
from shazam.stft import stft


def fingerprint_signal(
    signal: npt.NDArray[np.float32],
    config: DspConfig | None = None,
) -> list[tuple[int, int]]:
    """Turn a prepared signal into ``(hash, anchor_frame)`` pairs.

    Args:
        signal: Mono signal already at ``config.sample_rate``, i.e. something
            that came out of :func:`~shazam.audio.load`.
        config: DSP parameters.

    Returns:
        Every fingerprint the signal produces.
    """
    config = config or DspConfig()
    spectrogram = stft(signal, config).magnitude
    peaks = find_peaks(spectrogram, config)
    return list(generate_hashes(peaks, config))


def fingerprint_file(
    path: str | Path,
    config: DspConfig | None = None,
) -> list[tuple[int, int]]:
    """Load an audio file and fingerprint it.

    Raises:
        AudioLoadError: If the file cannot be decoded or is too short.
    """
    config = config or DspConfig()
    return fingerprint_signal(load(path, config), config)
