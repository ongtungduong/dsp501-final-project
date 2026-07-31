"""Render the spectrogram and constellation map of one track to PNG.

Numbers in a test suite prove the transform is self-consistent. They do not
show whether peak density is sane or whether peaks track the harmonics of the
music, and both of those are tuning decisions that have to be made by eye.

Usage:
    uv run python scripts/visualize_pipeline.py path/to/song.mp3
    uv run python scripts/visualize_pipeline.py song.mp3 --seconds 15 --out data/output
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # Render to file; no display needed.

import matplotlib.pyplot as plt
import numpy as np
import numpy.typing as npt

from shazam.audio import load
from shazam.config import DspConfig
from shazam.peaks import LOG_EPSILON, Peak, find_peaks
from shazam.stft import stft


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path, help="Audio file to analyse")
    parser.add_argument(
        "--seconds",
        type=float,
        default=10.0,
        help="How many seconds from the start to plot (default: 10)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/output"),
        help="Directory for the PNGs (default: data/output)",
    )
    args = parser.parse_args()

    config = DspConfig()
    signal = load(args.audio, config)

    max_samples = int(args.seconds * config.sample_rate)
    excerpt = signal[:max_samples]

    result = stft(excerpt, config)
    peaks = find_peaks(result.magnitude, config)

    duration = len(excerpt) / config.sample_rate
    density = len(peaks) / duration if duration else 0.0
    print(f"{args.audio.name}: {duration:.1f}s, {len(peaks)} peaks, {density:.1f} peaks/second")

    args.out.mkdir(parents=True, exist_ok=True)
    stem = args.audio.stem

    spectrum_db = 20.0 * np.log10(result.magnitude + LOG_EPSILON)
    extent = (0.0, duration, 0.0, config.sample_rate / 2.0)

    _plot(
        spectrum_db,
        extent,
        peaks=None,
        config=config,
        title=f"Spectrogram — {stem}",
        path=args.out / f"{stem}-spectrogram.png",
    )
    _plot(
        spectrum_db,
        extent,
        peaks=peaks,
        config=config,
        title=f"Constellation map — {stem} ({density:.1f} peaks/s)",
        path=args.out / f"{stem}-constellation.png",
    )


def _plot(
    spectrum_db: npt.NDArray[np.floating],
    extent: tuple[float, float, float, float],
    peaks: list[Peak] | None,
    config: DspConfig,
    title: str,
    path: Path,
) -> None:
    """Draw one spectrogram, optionally with its peaks marked."""
    fig, axes = plt.subplots(figsize=(14, 6))
    axes.imshow(
        spectrum_db,
        aspect="auto",
        origin="lower",
        extent=extent,
        cmap="magma",
        vmin=float(np.percentile(spectrum_db, 50)),
        vmax=float(spectrum_db.max()),
    )

    if peaks:
        times = [peak.frame * config.frame_duration for peak in peaks]
        freqs = [peak.freq_bin * config.bin_width_hz for peak in peaks]
        axes.scatter(times, freqs, s=6, c="cyan", marker="o", linewidths=0)

    axes.set_title(title)
    axes.set_xlabel("Time (s)")
    axes.set_ylabel("Frequency (Hz)")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
