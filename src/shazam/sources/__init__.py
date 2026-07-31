"""Music sources — where tracks and their metadata come from.

Adding a catalogue means adding one class that satisfies :class:`MusicSource`.
Nothing in the builder or the CLI needs to change for a new source.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class TrackMeta:
    """One track, as a catalogue describes it.

    Attributes:
        title: Track title. Falls back to the filename when unknown.
        artist: Performer, when the catalogue knows one.
        path: Location of the audio file on disk.
        source: Which catalogue this came from, e.g. ``"fma"`` or ``"local"``.
        genre: Genre label, when available.
    """

    title: str
    artist: str | None
    path: Path
    source: str
    genre: str | None = None


@runtime_checkable
class MusicSource(Protocol):
    """A catalogue of tracks that can be fetched and enumerated."""

    name: str

    def fetch(self, dest: Path) -> None:
        """Download whatever the source needs into ``dest``."""
        ...

    def tracks(self, root: Path) -> Iterator[TrackMeta]:
        """Yield the tracks available under ``root``."""
        ...
