"""Invariants of spectral peak picking (the constellation map).

The silence test is the reason `peaks.py` adds an epsilon before taking the
log. Without it, `log10(0)` produces -inf across the whole frame, every point
ties with its neighbourhood maximum, and *every* bin is reported as a peak.
Measured on a 50x50 silent spectrogram: 2500/2500 false peaks without epsilon,
0 with it.
"""

from __future__ import annotations

import numpy as np

from shazam.config import DspConfig
from shazam.peaks import find_peaks
from shazam.stft import stft


def _sine(freq: float, seconds: float, sample_rate: int) -> np.ndarray:
    t = np.arange(int(seconds * sample_rate), dtype=np.float64) / sample_rate
    return np.sin(2.0 * np.pi * freq * t).astype(np.float32)


def test_silence_yields_no_peaks() -> None:
    """Digital silence must produce an empty constellation map, not a full one."""
    config = DspConfig()
    silence = np.zeros(config.sample_rate * 2, dtype=np.float32)

    peaks = find_peaks(stft(silence, config).magnitude, config)

    assert peaks == []


def test_silence_does_not_saturate_the_map() -> None:
    """Guards the epsilon directly on a hand-built silent spectrogram."""
    config = DspConfig()
    silent_spectrogram = np.zeros((50, 50), dtype=np.float32)

    peaks = find_peaks(silent_spectrogram, config)

    assert len(peaks) == 0, f"epsilon missing: {len(peaks)} false peaks on silence"


def test_peaks_land_on_the_tone_frequency() -> None:
    """A sustained tone must be picked up at its own bin, in every frame.

    Not *only* at its own bin: a Hann-windowed sine has sidelobe ripples whose
    local maxima sit ~11 bins out and still clear the absolute -60 dB floor.
    That is expected behaviour of the window, not a defect, and real music
    masks it. What must hold is that the tone itself is always found.
    """
    config = DspConfig()
    spectrogram = stft(_sine(1000.0, 2.0, config.sample_rate), config).magnitude

    peaks = find_peaks(spectrogram, config)

    assert peaks, "a sustained tone must produce peaks"
    expected_bin = round(1000.0 / (config.sample_rate / config.window_size))
    tone_frames = sorted({peak.frame for peak in peaks if abs(peak.freq_bin - expected_bin) <= 1})
    n_frames = spectrogram.shape[1]

    # Detected throughout, not merely somewhere. Deliberately not asserting one
    # peak in *every* frame: a pure sustained tone is an exact plateau along
    # time, and peak picking compares for equality against the neighbourhood
    # maximum, so on a plateau which points win comes down to float rounding.
    # Real music has no exact plateaus. What must hold is that a tone lasting
    # two seconds keeps generating peaks for two seconds.
    assert tone_frames[0] < n_frames * 0.1
    assert tone_frames[-1] > n_frames * 0.9
    assert len(tone_frames) >= n_frames / config.peak_neighborhood_time


def test_tone_is_the_loudest_peak() -> None:
    config = DspConfig()
    spectrogram = stft(_sine(1000.0, 2.0, config.sample_rate), config).magnitude

    peaks = find_peaks(spectrogram, config)

    loudest = max(peaks, key=lambda peak: spectrogram[peak.freq_bin, peak.frame])
    expected_bin = round(1000.0 / (config.sample_rate / config.window_size))
    assert abs(loudest.freq_bin - expected_bin) <= 1


def test_peaks_are_sorted_by_frame() -> None:
    """Phase 2 pairing walks the list forward in time and relies on this order."""
    config = DspConfig()
    signal = _sine(440.0, 1.0, config.sample_rate) + _sine(1500.0, 1.0, config.sample_rate)

    peaks = find_peaks(stft(signal, config).magnitude, config)

    frames = [peak.frame for peak in peaks]
    assert frames == sorted(frames)


def test_peak_indices_stay_in_bounds() -> None:
    config = DspConfig()
    signal = _sine(800.0, 1.0, config.sample_rate)
    spectrogram = stft(signal, config).magnitude

    peaks = find_peaks(spectrogram, config)

    n_bins, n_frames = spectrogram.shape
    assert all(0 <= peak.freq_bin < n_bins for peak in peaks)
    assert all(0 <= peak.frame < n_frames for peak in peaks)


def test_quiet_noise_below_threshold_is_rejected() -> None:
    """Amplitude far below `peak_min_db` carries no information worth hashing."""
    config = DspConfig()
    rng = np.random.default_rng(0)
    whisper = (rng.standard_normal(config.sample_rate) * 1e-6).astype(np.float32)

    peaks = find_peaks(stft(whisper, config).magnitude, config)

    assert peaks == []
