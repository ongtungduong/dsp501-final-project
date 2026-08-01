"""Render one track's four fingerprinting steps to PNG.

Numbers in a test suite prove the transform is self-consistent. They do not
show whether peak density is sane or whether peaks track the harmonics of the
music, and both of those are tuning decisions that have to be made by eye.

Writes four figures: the resampling that step 1 performs, the spectrogram from
step 2, the constellation map from step 3, and the peak pairing from step 4.

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
import soundfile as sf
from matplotlib.patches import Rectangle

from shazam.audio import load
from shazam.config import DspConfig
from shazam.hashing import pack_hash
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
    _plot_resampling(args.audio, excerpt, config, args.out / f"{stem}-resampling.png")
    _plot_pairing(peaks, config, stem, args.out / f"{stem}-pairing.png")


def _plot_resampling(
    source: Path,
    processed: npt.NDArray[np.floating],
    config: DspConfig,
    path: Path,
) -> None:
    """Show what step 1 keeps and what it throws away.

    Plots the average spectrum of the file as delivered against the same
    measure after processing. The point is that everything above the new
    Nyquist is *removed* rather than folded back down as alias, which is the
    one thing a resampler can get wrong without the result sounding obviously
    broken.
    """
    original, source_rate = sf.read(source, always_2d=True)
    mono = original.mean(axis=1)
    # Match durations so the two averages describe the same passage of music.
    mono = mono[: int(len(processed) / config.sample_rate * source_rate)]

    fig, axes = plt.subplots(figsize=(14, 5))
    for samples, rate, label, colour in (
        (mono, source_rate, f"Trước — {source_rate} Hz", "#999999"),
        (processed, config.sample_rate, f"Sau — {config.sample_rate} Hz", "#d62728"),
    ):
        spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
        freqs = np.fft.rfftfreq(len(samples), 1.0 / rate)
        # Normalise by length so the two FFTs are comparable in level.
        axes.semilogy(freqs, spectrum / len(samples) + 1e-12, label=label, color=colour, lw=0.8)

    nyquist = config.sample_rate / 2.0
    axes.axvline(nyquist, color="#1f77b4", ls="--", lw=1.5, label=f"Nyquist mới — {nyquist:.0f} Hz")
    axes.set_xlim(0, source_rate / 2.0)
    axes.set_title(
        f"Bước 1 — hạ tần số lấy mẫu {source_rate} → {config.sample_rate} Hz "
        "(phần trên Nyquist bị lọc bỏ, không gập xuống)"
    )
    axes.set_xlabel("Tần số (Hz)")
    axes.set_ylabel("Biên độ trung bình")
    axes.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    print(f"wrote {path}")


def _plot_pairing(peaks: list[Peak], config: DspConfig, stem: str, path: Path) -> None:
    """Show step 4: one anchor peak paired with the peaks in its target zone.

    The constellation on its own does not explain why the fingerprint survives
    a clip starting at an arbitrary moment. What survives is the *shape*
    between two peaks, so the figure draws the zone and the pairs rather than
    the points alone.
    """
    min_frames = config.seconds_to_frames(config.min_time_delta)
    max_frames = config.seconds_to_frames(config.max_time_delta)

    # Pick an anchor that actually fills its fan-out, so the drawing shows the
    # normal case instead of a sparse edge of the track.
    anchor = None
    targets: list[Peak] = []
    for index, candidate in enumerate(peaks):
        zone = [
            peak
            for peak in peaks[index + 1 :]
            if min_frames <= peak.frame - candidate.frame <= max_frames
        ][: config.fan_out]
        if len(zone) >= config.fan_out:
            anchor, targets = candidate, zone
            break
    if anchor is None:
        print("no anchor with a full target zone; skipping pairing figure")
        return

    to_time = config.frame_duration
    to_hz = config.bin_width_hz

    fig, axes = plt.subplots(figsize=(14, 6))
    window = [
        peak
        for peak in peaks
        if anchor.frame - 20 <= peak.frame <= anchor.frame + max_frames + 20
    ]
    axes.scatter(
        [p.frame * to_time for p in window],
        [p.freq_bin * to_hz for p in window],
        s=18, c="#bbbbbb", label="Đỉnh khác",
    )
    axes.add_patch(
        Rectangle(
            ((anchor.frame + min_frames) * to_time, 0),
            (max_frames - min_frames) * to_time,
            config.sample_rate / 2.0,
            facecolor="#1f77b4", alpha=0.10, edgecolor="#1f77b4", ls="--",
            label=f"Vùng đích Δt ∈ [{config.min_time_delta}s, {config.max_time_delta}s]",
        )
    )
    for target in targets:
        axes.plot(
            [anchor.frame * to_time, target.frame * to_time],
            [anchor.freq_bin * to_hz, target.freq_bin * to_hz],
            color="#d62728", lw=1.2, zorder=3,
        )
    axes.scatter(
        [t.frame * to_time for t in targets],
        [t.freq_bin * to_hz for t in targets],
        s=45, c="#d62728", zorder=4, label=f"Đỉnh đích (fan-out {config.fan_out})",
    )
    axes.scatter(
        [anchor.frame * to_time], [anchor.freq_bin * to_hz],
        s=140, c="#2ca02c", marker="*", zorder=5, label="Đỉnh neo",
    )

    first = targets[0]
    example = pack_hash(anchor.freq_bin, first.freq_bin, first.frame - anchor.frame)
    axes.set_title(
        f"Bước 4 — ghép cặp đỉnh thành hash — {stem}\n"
        f"ví dụ: (f1={anchor.freq_bin}, f2={first.freq_bin}, "
        f"Δt={first.frame - anchor.frame} khung) → hash {example}"
    )
    axes.set_xlabel("Thời gian (s)")
    axes.set_ylabel("Tần số (Hz)")
    axes.legend(loc="upper right")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    print(f"wrote {path}")


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
