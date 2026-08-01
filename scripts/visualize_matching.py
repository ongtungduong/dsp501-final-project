"""Plot the time-offset histogram for a correct match and a wrong one, side by side.

This is the single picture that explains why the matcher works. Sharing hashes
with a track proves nothing — popular chords collide everywhere. What proves a
match is that the shared hashes all agree on *where* in the track the query
came from. A real excerpt produces one tall spike; an unrelated track produces
noise spread across every offset.

Usage:
    uv run python scripts/visualize_matching.py data/queries/q-10s.wav
    uv run python scripts/visualize_matching.py query.wav --out docs/images
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from shazam.audio import load
from shazam.config import DspConfig
from shazam.database import connect, fetch_song, lookup
from shazam.fingerprint import fingerprint_signal


def _offset_histogram(
    query_hashes: list[tuple[int, int]],
    hits: list[tuple[int, int, int]],
    song_id: int,
) -> Counter[int]:
    """Count how many distinct query hashes agree on each time offset for one track."""
    query_frames: dict[int, list[int]] = {}
    for hash_value, frame in query_hashes:
        query_frames.setdefault(hash_value, []).append(frame)

    agreeing: dict[int, set[int]] = {}
    for hash_value, hit_song, db_offset in hits:
        if hit_song != song_id:
            continue
        for query_frame in query_frames.get(hash_value, ()):
            agreeing.setdefault(db_offset - query_frame, set()).add(hash_value)

    return Counter({offset: len(hashes) for offset, hashes in agreeing.items()})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path, help="A query clip cut from a track in the corpus")
    parser.add_argument("--out", type=Path, default=Path("data/output"))
    args = parser.parse_args()

    config = DspConfig()
    hashes = fingerprint_signal(load(args.audio, config), config)

    with connect() as conn:
        hits = lookup(conn, [h for h, _ in hashes])
        if not hits:
            raise SystemExit("No hashes matched the corpus — build it first.")

        # Rank tracks by how much they agree, then take the best and a
        # mid-ranking one. The runner-up is the honest comparison: it shares
        # real hashes with the query and still fails to line them up.
        totals = Counter(song_id for _, song_id, _ in hits)
        ranked = totals.most_common()
        best_id = ranked[0][0]
        wrong_id = ranked[len(ranked) // 2][0] if len(ranked) > 1 else None

        names = {song_id: fetch_song(conn, song_id) for song_id, _ in ranked}

    fig = Figure(figsize=(13, 4.5), dpi=130)
    FigureCanvasAgg(fig)
    # Separate y-scales on purpose. Sharing them makes the wrong track's panel
    # look empty, which hides the actual finding: it does share hashes, they
    # just scatter across every offset instead of agreeing on one. The contrast
    # lives in the shape and in the peak heights printed on each title.
    axes = fig.subplots(1, 2)

    for axis, song_id, label in (
        (axes[0], best_id, "Bài đúng"),
        (axes[1], wrong_id, "Bài sai"),
    ):
        if song_id is None:
            axis.set_visible(False)
            continue

        histogram = _offset_histogram(hashes, hits, song_id)
        offsets = np.array(sorted(histogram))
        counts = np.array([histogram[offset] for offset in offsets])
        seconds = offsets * config.frame_duration

        axis.bar(seconds, counts, width=config.frame_duration * 2, color="#e0245e")
        title = names.get(song_id)
        name = title[0] if title else f"song {song_id}"
        axis.set_title(
            f"{label} — {name}\n"
            f"đỉnh cao nhất {counts.max()} hash, tổng {counts.sum()} hash trùng"
        )
        axis.set_xlabel("Độ lệch thời gian (giây)")
        axis.set_ylabel("Số hash đồng thuận")
        axis.margins(y=0.15)
    fig.suptitle(
        "Histogram độ lệch thời gian: bài đúng cho một đỉnh nhọn, bài sai rải đều",
        fontsize=12,
    )
    fig.tight_layout()

    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / "histogram-khop.png"
    fig.savefig(path)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
