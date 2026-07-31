"""Spectral peak picking — the constellation map.

Why peaks at all: absolute magnitudes do not survive the trip through a
loudspeaker, a room and a phone microphone, nor through lossy compression. The
*locations* of local spectral maxima largely do. Reducing a spectrogram to a
sparse set of (time, frequency) points therefore throws away almost everything
except the part that is robust — and the sparsity is what makes the database
tractable.
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np
import numpy.typing as npt
from scipy.ndimage import maximum_filter

from shazam.config import DspConfig

# Guards log10 against a zero magnitude. Digital silence is not hypothetical:
# it appears in leading and trailing frames of ordinary tracks.
#
# Without this, log10(0) is -inf. Every point in a silent frame then ties with
# its neighbourhood maximum *and* -inf compares as greater than nothing, so the
# frame reports every single bin as a peak — measured 2500/2500 on a 50x50
# silent spectrogram. With the epsilon, silence maps to a finite -200 dB, which
# the peak_min_db floor rejects.
#
# Note what actually does the work: the epsilon makes silence *finite* so the
# floor can reject it. It does not break ties. A perfectly flat but loud region
# would still report every bin, since each point genuinely equals its
# neighbourhood maximum. Real audio never produces exact float32 plateaus, so
# this is theoretical — but it is the floor, not the epsilon, that discards
# silent frames.
LOG_EPSILON = 1e-10


class Peak(NamedTuple):
    """A single point of the constellation map.

    Attributes:
        frame: STFT frame index — time.
        freq_bin: STFT frequency bin index — frequency.
    """

    frame: int
    freq_bin: int


def find_peaks(
    magnitude: npt.NDArray[np.float32],
    config: DspConfig | None = None,
) -> list[Peak]:
    """Reduce a magnitude spectrogram to its local maxima.

    A point is kept when it is the largest value in a
    ``peak_neighborhood_freq`` x ``peak_neighborhood_time`` box around it, and
    when it also clears the ``peak_min_db`` floor. The first condition finds
    structure; the second discards structure that is merely the loudest thing
    in a near-silent region.

    The floor is in dB relative to full scale, because :func:`~shazam.stft.stft`
    divides out the window's coherent gain. That makes the threshold independent
    of ``window_size`` and gives it a meaning that can be reasoned about: -60
    means 60 dB below a full-scale sinusoid.

    Note that an even neighbourhood size is not symmetric — SciPy's
    ``maximum_filter`` with ``size=20`` spans 10 bins back and 9 forward. The
    filter is applied identically everywhere, so this biases nothing; use an odd
    size if exact centring ever matters.

    Args:
        magnitude: Magnitude spectrogram shaped ``(n_bins, n_frames)``.
        config: DSP parameters. Defaults to :class:`DspConfig`.

    Returns:
        Peaks sorted by frame, then by frequency bin. Phase 2 pairs each anchor
        with the peaks that follow it and depends on this ordering.
    """
    config = config or DspConfig()

    if magnitude.size == 0:
        return []

    spectrum_db = 20.0 * np.log10(magnitude + LOG_EPSILON)

    neighborhood = (config.peak_neighborhood_freq, config.peak_neighborhood_time)
    local_maxima = maximum_filter(spectrum_db, size=neighborhood, mode="constant", cval=-np.inf)

    is_peak = (spectrum_db == local_maxima) & (spectrum_db > config.peak_min_db)

    # argwhere yields (freq_bin, frame) pairs in row-major order, i.e. sorted
    # by frequency. Re-sort into time order for the pairing stage.
    coordinates = np.argwhere(is_peak)
    peaks = [Peak(frame=int(frame), freq_bin=int(freq_bin)) for freq_bin, frame in coordinates]
    peaks.sort()
    return peaks
