"""Short-Time Fourier Transform, implemented directly on ``numpy.fft``.

This module deliberately does not call ``scipy.signal.stft`` or
``scipy.signal.spectrogram``. Writing the transform out is the point of the
exercise, and a test asserts that no SciPy import creeps back in here.

The transform in one line: cut the signal into overlapping frames, taper each
frame with a Hann window, and take the real-input FFT of every frame.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from shazam.config import DspConfig


@dataclass(frozen=True)
class StftResult:
    """Output of :func:`stft`.

    Attributes:
        magnitude: Magnitude spectrogram, shaped ``(n_bins, n_frames)``,
            scaled so that a full-scale sinusoid reads 1.0 at its own bin.
            Frequency runs down the rows so that a spectrogram prints the way
            it is drawn, low frequencies at index 0.
        freqs: Centre frequency of each bin, in Hz. Length ``n_bins``.
        times: Start time of each frame, in seconds. Length ``n_frames``.
    """

    magnitude: npt.NDArray[np.float32]
    freqs: npt.NDArray[np.float64]
    times: npt.NDArray[np.float64]


def stft(signal: npt.NDArray[np.float32], config: DspConfig | None = None) -> StftResult:
    """Compute the magnitude spectrogram of a mono signal.

    Why a Hann window: chopping a signal into frames multiplies it by a
    rectangular window, whose spectrum is a sinc with slowly decaying
    sidelobes. A tone that does not sit exactly on a bin centre then smears
    energy across the whole spectrum — spectral leakage — and buries the
    smaller peaks we need. Hann tapers each frame smoothly to zero at both
    ends, trading a slightly wider main lobe for far lower sidelobes.

    Why ``rfft`` and not ``fft``: audio is a real-valued signal, so its
    spectrum is conjugate-symmetric and the upper half carries no information
    the lower half does not. ``rfft`` returns only bins 0..N/2, which halves
    both the computation and the memory.

    Why the output is scaled by the window's coherent gain: a raw ``rfft``
    magnitude carries a factor of ``sum(window)/2`` — about 256 here, or +48 dB
    — and that factor grows with ``window_size``. Dividing it out makes the
    magnitudes read relative to full scale, so a dB threshold means the same
    thing regardless of window length. Without it, ``peak_min_db`` would drift
    by 6 dB every time the window size doubled.

    Args:
        signal: Mono signal, already at ``config.sample_rate``.
        config: DSP parameters. Defaults to :class:`DspConfig`.

    Returns:
        The magnitude spectrogram together with its frequency and time axes.
    """
    config = config or DspConfig()
    window_size = config.window_size
    hop_size = config.hop_size

    freqs = np.fft.rfftfreq(window_size, d=1.0 / config.sample_rate)

    # A signal shorter than one window yields no frames at all. Handled up
    # front because sliding_window_view raises rather than returning empty.
    if signal.shape[0] < window_size:
        return StftResult(
            magnitude=np.empty((config.n_bins, 0), dtype=np.float32),
            freqs=freqs,
            times=np.empty(0, dtype=np.float64),
        )

    # sliding_window_view produces every possible window as a *view*, and the
    # stride then selects one per hop — neither step copies. The multiply below
    # is the first allocation: it materialises an (n_frames, window_size)
    # float32 array, which the rfft then matches with a complex64 one. Budget
    # for roughly both when sizing a parallel build.
    all_windows = np.lib.stride_tricks.sliding_window_view(signal, window_size)
    frames = all_windows[::hop_size]

    window = np.hanning(window_size).astype(np.float32)
    spectrum = np.fft.rfft(frames * window, axis=-1)

    # Divide out the window's coherent gain so a full-scale sinusoid reads 1.0,
    # then transpose so frequency is the row axis: (n_frames, n_bins) -> (n_bins, n_frames).
    # ascontiguousarray undoes the transpose's stride reversal; without it every
    # downstream row-wise pass (maximum_filter, per-frame slicing) fights the
    # memory layout and SciPy copies the array anyway.
    coherent_gain = float(np.sum(window)) / 2.0
    magnitude = np.ascontiguousarray((np.abs(spectrum) / coherent_gain).astype(np.float32).T)
    times = np.arange(frames.shape[0], dtype=np.float64) * hop_size / config.sample_rate

    return StftResult(magnitude=magnitude, freqs=freqs, times=times)
