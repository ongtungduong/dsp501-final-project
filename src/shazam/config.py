"""Every DSP parameter in the system, in one place.

Scattering these as magic numbers across modules is how the corpus builder and
the query path drift apart. Both must read the same configuration object.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DspConfig:
    """Signal-processing parameters shared by fingerprinting and matching.

    Attributes:
        sample_rate: Working rate in Hz. 11025 is exactly 44100/4, so CD-rate
            audio decimates by an integer factor. Nyquist at 5512 Hz still
            covers the frequency band where musical energy lives.
        window_size: STFT window length in samples. 1024 samples is 92.9 ms and
            gives 10.77 Hz of frequency resolution — fine enough to separate
            harmonics of adjacent notes.
        hop_size: Advance between windows, in samples. 256 gives 75% overlap;
            dense enough that spectral peaks stay put when a query is not
            frame-aligned with the recording it came from.
        peak_neighborhood_freq: Height, in frequency bins, of the box a point
            must dominate to count as a peak.
        peak_neighborhood_time: Width, in frames, of that same box. Together
            these two yield roughly 20-30 peaks per second on real music.
        peak_min_db: Absolute floor in dB. Points below this carry no
            information worth hashing, however locally dominant they are.
        fan_out: How many later peaks each anchor peak is paired with.
        min_time_delta: Closest pairing distance in seconds. Pairs tighter than
            this are too easily disturbed by noise.
        max_time_delta: Furthest pairing distance in seconds. Together with
            min_time_delta this defines the target zone ahead of each anchor.
    """

    sample_rate: int = 11025
    window_size: int = 1024
    hop_size: int = 256
    peak_neighborhood_freq: int = 20
    peak_neighborhood_time: int = 20
    peak_min_db: float = -60.0
    fan_out: int = 8
    min_time_delta: float = 0.1
    max_time_delta: float = 2.0

    @property
    def n_bins(self) -> int:
        """Number of frequency bins produced by a real-input FFT."""
        return self.window_size // 2 + 1

    @property
    def bin_width_hz(self) -> float:
        """Frequency spacing between adjacent bins, in Hz."""
        return self.sample_rate / self.window_size

    @property
    def frame_duration(self) -> float:
        """Time between the start of consecutive frames, in seconds."""
        return self.hop_size / self.sample_rate

    def seconds_to_frames(self, seconds: float) -> int:
        """Convert a duration in seconds to a whole number of STFT frames."""
        return round(seconds / self.frame_duration)
