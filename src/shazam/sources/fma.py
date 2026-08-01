"""Free Music Archive ``fma_small`` catalogue.

Downloads two zip archives from the FMA mirror — 8000 audio tracks and a
metadata table for the full 106574-track archive — extracts them, and joins
each track's audio file to its real title, artist, and genre.

Adding this source required no change to ``builder.py`` or ``cli.py`` beyond
wiring: it satisfies :class:`shazam.sources.MusicSource` the same way
:class:`shazam.sources.local.LocalSource` does.
"""

from __future__ import annotations

import hashlib
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx

# pandas ships no type information (no py.typed, no bundled stubs), and adding
# pandas-stubs would mean editing the shared pyproject.toml that other agents
# are working in concurrently. The import is scoped to this one module, and
# every value pulled out of a DataFrame below is immediately coerced through
# str()/pd.isna() before it reaches a typed field.
import pandas as pd  # type: ignore[import-untyped]

from shazam.sources import TrackMeta

BASE_URL = "https://os.unil.cloud.switch.ch/fma/"
# 1 MiB: large enough that per-chunk overhead is negligible against a 7.2 GiB
# transfer, small enough for the progress line to update responsively.
CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class _Archive:
    """One zip this source needs, and how to tell it downloaded intact.

    Attributes:
        filename: Name on both the remote server and local disk.
        sha1: Published checksum of the complete file.
        extracted_marker: Path, relative to the destination directory, that
            only exists once this archive has been extracted. Used to skip
            re-extracting a multi-gigabyte zip on every run.
    """

    filename: str
    sha1: str
    extracted_marker: str


ARCHIVES = (
    _Archive("fma_small.zip", "ade154f733639d52e35e32f5593efe5be76c6d70", "fma_small"),
    _Archive(
        "fma_metadata.zip",
        "f0df49ffe5f2a6008d7dc83c6915b31835dfe733",
        "fma_metadata/tracks.csv",
    ),
)


class FmaSourceError(RuntimeError):
    """A downloaded archive failed its checksum."""


class FmaSource:
    """The Free Music Archive ``fma_small`` catalogue: 8000 tracks x 30s, 8 genres."""

    name = "fma"

    def fetch(self, dest: Path) -> None:
        """Download and extract both archives into ``dest``.

        Resumes a partial download via the ``Range`` header, verifies SHA1
        after every completed download, and skips work — download or
        extraction — that a previous run already finished successfully.

        Args:
            dest: Directory to hold the zips and their extracted contents.
                Created if missing.

        Raises:
            FmaSourceError: A downloaded archive's SHA1 does not match the
                published checksum. The bad file is deleted before raising,
                so a re-run starts a fresh download rather than resuming
                corrupt bytes.
        """
        dest.mkdir(parents=True, exist_ok=True)

        with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30.0, read=120.0)) as client:
            for archive in ARCHIVES:
                archive_path = dest / archive.filename
                _download_resumable(client, BASE_URL + archive.filename, archive_path, archive.sha1)

                # A sentinel written only after extractall returns, rather than
                # testing for the output directory. The directory appears the
                # moment the first member is written, so an interrupted
                # extraction — Ctrl-C or a full disk partway through 7 GB —
                # would look complete on the next run. The catalogue would then
                # be built from whatever fraction of the tracks made it, and
                # tracks() would report the rest as ordinary metadata mismatches.
                sentinel = dest / f".{archive.filename}.extracted"
                if sentinel.exists():
                    print(f"{archive.filename}: already extracted, skipping")
                    continue
                print(f"Extracting {archive.filename}...")
                with zipfile.ZipFile(archive_path) as zf:
                    zf.extractall(dest)
                sentinel.write_text(archive.sha1)

    def tracks(self, root: Path) -> Iterator[TrackMeta]:
        """Yield every ``fma_small`` track that has both an audio file and metadata.

        Reads ``fma_metadata/tracks.csv`` under ``root`` and, for each row,
        builds the audio path from the track id and checks it exists. Rows
        whose audio file is missing (the metadata covers all 106574 FMA
        tracks, not just the 8000 in ``fma_small``) or whose title is blank
        are skipped, and the count of each is logged.

        Args:
            root: Directory ``fetch`` extracted into, e.g. ``data/fma``.

        Yields:
            One :class:`TrackMeta` per track with both a file and a title.
        """
        metadata_csv = root / "fma_metadata" / "tracks.csv"
        if not metadata_csv.exists():
            print(f"fma: no metadata at {metadata_csv} — run `shazam fetch --source fma` first")
            return

        audio_dir = root / "fma_small"
        table = pd.read_csv(metadata_csv, header=[0, 1], index_col=0)

        missing_file = 0
        missing_title = 0
        yielded = 0
        for track_id, row in table.iterrows():
            path = track_path(audio_dir, int(track_id))
            if not path.is_file():
                missing_file += 1
                continue

            title = _clean(row[("track", "title")])
            if title is None:
                missing_title += 1
                continue

            yield TrackMeta(
                title=title,
                artist=_clean(row[("artist", "name")]),
                path=path.resolve(),
                source=self.name,
                genre=_clean(row[("track", "genre_top")]),
                # The track id is already a stable catalogue-wide identifier, so
                # the key needs nothing from the filesystem layout and stays the
                # same on the host and inside the container. See
                # TrackMeta.catalogue_key.
                key=f"{self.name}:{int(track_id):06d}",
            )
            yielded += 1

        print(
            f"fma: {yielded} tracks ready "
            f"({missing_file} skipped: no audio file under fma_small/, "
            f"{missing_title} skipped: no title in metadata)"
        )


def track_path(audio_root: Path, track_id: int) -> Path:
    """Where a track's audio file lives under FMA's numbering convention.

    FMA buckets files by the first three digits of the zero-padded six-digit
    id, e.g. track id 2 lives at ``fma_small/000/000002.mp3``.

    Args:
        audio_root: The ``fma_small`` directory.
        track_id: FMA track id, as it appears in ``tracks.csv``.

    Returns:
        Expected path, not checked for existence.
    """
    padded = f"{track_id:06d}"
    return audio_root / padded[:3] / f"{padded}.mp3"


def _clean(value: object) -> str | None:
    """Normalise a pandas cell to a trimmed string, or ``None`` if empty/NaN."""
    if pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _download_resumable(client: httpx.Client, url: str, path: Path, expected_sha1: str) -> None:
    """Download ``url`` to ``path``, resuming and verifying as needed.

    Skips entirely when ``path`` already holds the complete, checksum-valid
    file. Resumes via ``Range`` when a smaller partial file is present. Any
    other case downloads from byte zero.

    Args:
        client: Shared HTTP client.
        url: Full URL to download.
        path: Local destination.
        expected_sha1: Published checksum the finished file must match.

    Raises:
        FmaSourceError: The finished download's SHA1 does not match. The
            file is deleted before raising.
    """
    remote_size = _remote_size(client, url)
    local_size = path.stat().st_size if path.exists() else 0

    if local_size > 0 and local_size >= remote_size:
        if _sha1_of(path) == expected_sha1:
            print(f"{path.name}: already downloaded and verified, skipping")
            return
        print(f"{path.name}: local file complete but checksum mismatch, redownloading")
        local_size = 0

    resuming = local_size > 0
    headers = {"Range": f"bytes={local_size}-"} if resuming else {}
    if resuming:
        print(f"{path.name}: resuming from {local_size / 1_000_000:.0f} MB")

    with client.stream("GET", url, headers=headers) as response:
        response.raise_for_status()
        # A server is allowed to ignore Range and answer 200 with the whole
        # body. Falling back to a fresh write is what makes that safe instead
        # of silently appending the full file onto an existing partial one.
        honoured_resume = resuming and response.status_code == httpx.codes.PARTIAL_CONTENT
        downloaded = local_size if honoured_resume else 0
        mode = "ab" if honoured_resume else "wb"

        with path.open(mode) as handle:
            for chunk in response.iter_bytes(CHUNK_SIZE):
                handle.write(chunk)
                downloaded += len(chunk)
                _print_progress(path.name, downloaded, remote_size)
    print()

    actual_sha1 = _sha1_of(path)
    if actual_sha1 != expected_sha1:
        path.unlink()
        raise FmaSourceError(
            f"{path.name}: SHA1 mismatch (expected {expected_sha1}, got {actual_sha1}) "
            "— file deleted, re-run to try again"
        )


def _remote_size(client: httpx.Client, url: str) -> int:
    """Content-Length of ``url``, via HEAD."""
    response = client.head(url)
    response.raise_for_status()
    return int(response.headers["content-length"])


def _sha1_of(path: Path) -> str:
    """SHA1 digest of a file, read in chunks so multi-gigabyte files don't load fully."""
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _print_progress(name: str, downloaded: int, total: int) -> None:
    """Overwrite a single progress line rather than flooding stdout per chunk."""
    pct = downloaded / total * 100 if total else 100.0
    print(
        f"\r{name}: {downloaded / 1e9:.2f} / {total / 1e9:.2f} GB ({pct:.1f}%)",
        end="",
        flush=True,
    )
