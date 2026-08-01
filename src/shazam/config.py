"""Every DSP parameter in the system, in one place.

Scattering these as magic numbers across modules is how the corpus builder and
the query path drift apart. Both must read the same configuration object.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_settings import BaseSettings, SettingsConfigDict


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


@dataclass(frozen=True)
class MatchConfig:
    """Thresholds deciding whether a query counts as recognised.

    Design decision #2. Taking the tallest offset histogram peak on its own
    always returns *some* track, even for white noise, so two conditions must
    hold together. With 8000 tracks in the corpus the chance of a plausible
    wrong answer is far higher than it would be with a few dozen.

    Attributes:
        min_score: Fewest aligned hashes that can count as a match. Guards
            against a query that resembles nothing in the corpus.
        score_ratio: How far ahead of the runner-up the winner must be. Guards
            against a query that resembles two tracks equally, where picking
            either would be a coin flip. Skipped when there is only one
            candidate, since there is nothing to compare against.

    ``min_score`` is 20 rather than 10 because of measurements on the full
    8000-track corpus, using held-out tracks as impostors — real music from the
    same collection, not noise. Correct tracks scored 222 at the lowest and 1205
    at the median; impostors sat at a median of 6 and a 95th percentile of 13.
    Raising the floor from 10 to 20 halved the false accepts (6 of 120 down to
    3) and cost no correct match, since the nearest genuine score is still more
    than ten times the threshold.

    Do not raise it much further. Nothing above 20 removed another false accept
    — the three that remain are duplicate recordings that exist twice in the
    corpus under different ids, which no score threshold can separate. Meanwhile
    a microphone recording scores far lower than a clip cut from a file, so a
    high floor buys nothing here and quietly costs real-world recall.
    """

    min_score: int = 20
    score_ratio: float = 2.0


class DatabaseSettings(BaseSettings):
    """Database connection settings, read from the environment.

    Validated at construction so a missing or malformed ``DATABASE_URL`` fails
    immediately with a clear message, rather than surfacing as a connection
    error on the first query. Phase 7 runs this in a container where there is
    no local config file to fall back on.

    Attributes:
        database_url: libpq connection string for the fingerprint database.
        pool_min_size: Connections opened eagerly when the pool starts.
        pool_max_size: Ceiling on concurrent connections.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://shazam:shazam@localhost:5432/shazam"
    pool_min_size: int = 1
    pool_max_size: int = 10
