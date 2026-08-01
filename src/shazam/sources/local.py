"""Audio files dropped into a local directory by hand.

Needed for demonstrating in front of an audience with music people recognise,
which 8000 unfamiliar Free Music Archive tracks cannot do.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from shazam.sources import TrackMeta

AUDIO_SUFFIXES = frozenset({".mp3", ".wav", ".flac", ".m4a", ".ogg"})


class LocalSource:
    """Tracks found by scanning a directory tree."""

    name = "local"

    def fetch(self, dest: Path) -> None:
        """Nothing to download — the files are already there."""
        dest.mkdir(parents=True, exist_ok=True)

    def tracks(self, root: Path) -> Iterator[TrackMeta]:
        """Yield every audio file under ``root``, filename as the title."""
        if not root.exists():
            return

        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES:
                yield TrackMeta(
                    title=path.stem,
                    artist=None,
                    # Absolute, so the same file yields the same catalogue key
                    # regardless of the working directory or whether the caller
                    # passed a relative --songs-dir. The database's uniqueness
                    # constraint on path is what makes a build resumable.
                    path=path.resolve(),
                    source=self.name,
                )
