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
        path: Location of the audio file on disk, for opening it now.
        source: Which catalogue this came from, e.g. ``"fma"`` or ``"local"``.
        genre: Genre label, when available.
        key: Stable identifier for this track within its catalogue, used to
            recognise a track that is already in the database. See :meth:`key`.
    """

    title: str
    artist: str | None
    path: Path
    source: str
    genre: str | None = None
    key: str = ""

    def catalogue_key(self) -> str:
        """Identify this track across machines and mount points.

        The database's uniqueness constraint is what lets an interrupted build
        resume, so the identifier has to be the same string wherever the build
        runs. An absolute path is not: the same file is
        ``/Users/me/project/data/songs/x.wav`` on the host and
        ``/app/data/songs/x.wav`` in the container, so building in both — which
        the Docker workflow explicitly invites — inserts every track twice.

        That failure is quiet and damaging. Two copies of a track split the
        offset histogram between them, the runner-up ratio then sees two equally
        good candidates, and a song that is definitely in the corpus comes back
        as "not found".

        Sources therefore supply a key relative to their own root, prefixed by
        the catalogue name, e.g. ``local:00-a-minor.wav``.
        """
        return self.key or f"{self.source}:{self.path.name}"


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
