"""Invariants of the hand-rolled STFT.

The whole academic point of this project is that the STFT is implemented from
`numpy.fft`, not delegated to SciPy. `test_stft_does_not_delegate_to_scipy`
guards that, so the constraint survives future refactors.
"""

from __future__ import annotations

import ast
import inspect

import numpy as np

from shazam import stft as stft_module
from shazam.config import DspConfig
from shazam.stft import stft


def _sine(freq: float, seconds: float, sample_rate: int) -> np.ndarray:
    t = np.arange(int(seconds * sample_rate), dtype=np.float64) / sample_rate
    return np.sin(2.0 * np.pi * freq * t).astype(np.float32)


def test_440hz_sine_peaks_at_bin_41() -> None:
    """440 Hz at 11025 Hz / 1024-point window lands in bin 41 (= 441.4 Hz).

    Bin width is 11025/1024 = 10.7666 Hz, so 440/10.7666 = 40.87 rounds to 41.
    This number was measured before the plan was written; it is the single
    cheapest proof that the transform is wired up correctly.
    """
    config = DspConfig()
    signal = _sine(440.0, 1.0, config.sample_rate)

    result = stft(signal, config)
    dominant_bin = int(np.argmax(result.magnitude.mean(axis=1)))

    assert dominant_bin == 41


def test_magnitude_shape_matches_frame_formula() -> None:
    config = DspConfig()
    signal = _sine(440.0, 2.0, config.sample_rate)

    result = stft(signal, config)

    expected_frames = 1 + (len(signal) - config.window_size) // config.hop_size
    expected_bins = config.window_size // 2 + 1
    assert result.magnitude.shape == (expected_bins, expected_frames)
    assert result.freqs.shape == (expected_bins,)
    assert result.times.shape == (expected_frames,)


def test_freq_axis_spans_zero_to_nyquist() -> None:
    config = DspConfig()
    result = stft(_sine(440.0, 1.0, config.sample_rate), config)

    assert result.freqs[0] == 0.0
    assert result.freqs[-1] == config.sample_rate / 2


def test_time_axis_advances_by_hop_duration() -> None:
    config = DspConfig()
    result = stft(_sine(440.0, 1.0, config.sample_rate), config)

    hop_seconds = config.hop_size / config.sample_rate
    assert result.times[0] == 0.0
    np.testing.assert_allclose(np.diff(result.times), hop_seconds, rtol=1e-6)


def test_two_tones_produce_two_peaks() -> None:
    """A sum of two sines must resolve as two distinct spectral peaks."""
    config = DspConfig()
    signal = _sine(440.0, 1.0, config.sample_rate) + _sine(1000.0, 1.0, config.sample_rate)

    spectrum = stft(signal, config).magnitude.mean(axis=1)
    bin_width = config.sample_rate / config.window_size
    assert spectrum[round(440.0 / bin_width)] > spectrum.mean() * 10
    assert spectrum[round(1000.0 / bin_width)] > spectrum.mean() * 10


def test_signal_shorter_than_window_yields_no_frames() -> None:
    config = DspConfig()
    result = stft(np.zeros(config.window_size - 1, dtype=np.float32), config)

    assert result.magnitude.shape[1] == 0
    assert result.times.shape == (0,)


def test_stft_does_not_delegate_to_scipy() -> None:
    """The transform must be ours.

    Checked against the parsed imports rather than the raw text, so that the
    module docstring stays free to explain *why* SciPy is not used here.
    """
    tree = ast.parse(inspect.getsource(stft_module))

    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    assert not [name for name in imported if name.split(".")[0] == "scipy"]


def test_full_scale_sinusoid_reads_unity() -> None:
    """Magnitudes are scaled relative to full scale, not left at raw FFT gain.

    This is what makes `peak_min_db` mean the same thing at any window size.
    """
    config = DspConfig()
    result = stft(_sine(1000.0, 1.0, config.sample_rate), config)

    assert 0.95 <= float(result.magnitude.max()) <= 1.05


def test_magnitude_scale_is_independent_of_window_size() -> None:
    """A full-scale tone reads ~1.0 at any window size.

    The tolerance covers scalloping loss: a tone that falls between two bin
    centres splits its energy across them, costing up to 1.42 dB (a factor of
    0.85) with a Hann window. That is a property of the transform, not of the
    scaling, and it moves with window size because the bin grid does.
    """
    signal = _sine(1000.0, 1.0, 11025)

    for size in (512, 1024, 2048):
        config = DspConfig(window_size=size, hop_size=size // 4)
        peak = float(stft(signal, config).magnitude.max())
        assert 0.84 <= peak <= 1.05, f"window {size} reads {peak:.3f}"
