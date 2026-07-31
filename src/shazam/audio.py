"""Loading and normalising audio into the one signal path the system uses.

Design decision #1: resampling happens here and nowhere else, through exactly
one filter. The corpus builder, the CLI, the microphone recorder and the HTTP
endpoint all funnel through :func:`_process`. If any caller resampled on its
own — or if this module used a different filter depending on the source rate —
query fingerprints would be computed from a subtly different signal than the
stored ones. Recognition would degrade while every component still looked
healthy, which is close to undebuggable.
"""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

import numpy as np
import numpy.typing as npt
import soundfile as sf
from scipy.signal import resample_poly

from shazam.config import DspConfig


class AudioLoadError(Exception):
    """Raised when audio cannot be decoded or is too short to fingerprint."""


def load(path: str | Path, config: DspConfig | None = None) -> npt.NDArray[np.float32]:
    """Load an audio file as mono at the configured sample rate.

    Args:
        path: Path to any format libsndfile can decode (MP3, WAV, FLAC, ...).
        config: DSP parameters. Defaults to :class:`DspConfig`.

    Returns:
        A 1-D float32 signal at ``config.sample_rate``, normalised to [-1, 1].

    Raises:
        AudioLoadError: If the file cannot be decoded or yields too few samples.
    """
    return _read(str(path), config or DspConfig())


def _read(
    source: str | BinaryIO,
    config: DspConfig,
) -> npt.NDArray[np.float32]:
    """Decode ``source`` and run it through the canonical signal path.

    Both the file entry point and Phase 4's in-memory entry point share this
    function, so neither can drift from the other's decode settings or error
    contract.

    Args:
        source: A filesystem path, or any binary stream libsndfile can read.
        config: DSP parameters.

    Returns:
        A 1-D float32 signal at ``config.sample_rate``, normalised to [-1, 1].

    Raises:
        AudioLoadError: If decoding fails or the result is too short.
    """
    try:
        data, sample_rate = sf.read(source, dtype="float32", always_2d=True)
    except Exception as exc:  # libsndfile surfaces several unrelated types
        raise AudioLoadError("Cannot decode audio") from exc

    return _process(data, int(sample_rate), config)


def _process(
    data: npt.NDArray[np.float32],
    sample_rate: int,
    config: DspConfig,
) -> npt.NDArray[np.float32]:
    """Convert decoded samples to the canonical mono signal.

    This is the single point every entry into the system must pass through.

    Args:
        data: Decoded samples shaped ``(n_samples, n_channels)``.
        sample_rate: Rate of ``data`` in Hz.
        config: DSP parameters.

    Returns:
        A 1-D float32 signal at ``config.sample_rate``, normalised to [-1, 1].

    Raises:
        AudioLoadError: If the signal is too short to produce a single STFT frame.
    """
    if data.size == 0:
        raise AudioLoadError("Audio contains no samples")

    mono = data.mean(axis=1).astype(np.float32)
    resampled = _resample(mono, sample_rate, config.sample_rate)

    # Fewer samples than one window means zero STFT frames, hence zero peaks.
    # Failing loudly here beats returning something that silently fingerprints
    # to nothing, and it keeps the error inside this module's contract.
    if resampled.shape[0] < config.window_size:
        seconds = resampled.shape[0] / config.sample_rate
        raise AudioLoadError(
            f"Audio too short to fingerprint: {seconds:.3f}s, "
            f"need at least {config.window_size / config.sample_rate:.3f}s"
        )

    return _normalise(resampled)


def _resample(
    signal: npt.NDArray[np.float32],
    source_rate: int,
    target_rate: int,
) -> npt.NDArray[np.float32]:
    """Move a signal to ``target_rate`` through a single, fixed filter.

    ``resample_poly`` reduces the ratio by its GCD first, so the common
    44100 -> 11025 case becomes up-1 / down-4: plain integer decimation, just
    as cheap as ``scipy.signal.decimate``, but with a Kaiser FIR that stays
    flat to near Nyquist. 48000 -> 11025 (what a browser AudioContext hands us
    on macOS) becomes up-147 / down-640 through the same filter design.

    Deliberately *not* branching to ``decimate`` for integer ratios. Its default
    order-8 Chebyshev-I IIR rolls off from ~4.4 kHz, well below our 5512 Hz
    Nyquist, so it would delete the top fifth of the spectrum on 44.1 kHz input
    only — measured -12 dB at 4800 Hz and -23.8 dB at 5000 Hz against -0.2 dB
    and -0.8 dB here. Corpus and query would then be filtered differently, which
    is exactly what design decision #1 exists to prevent.

    Dropping samples without filtering at all would alias every frequency above
    the new Nyquist back down into the audible band as phantom tones.
    """
    if source_rate == target_rate:
        return signal
    return np.asarray(resample_poly(signal, target_rate, source_rate), dtype=np.float32)


def _normalise(signal: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
    """Scale peak amplitude to 1.0 so loudness cannot shift the peak threshold.

    A quiet recording of a track must fingerprint to the same constellation as
    a loud one. Silence is returned untouched rather than divided by zero.
    """
    peak = float(np.max(np.abs(signal))) if signal.size else 0.0
    if peak == 0.0:
        return signal
    return (signal / peak).astype(np.float32)
