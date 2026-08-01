"""Music catalogue sources: local files and the Free Music Archive.

No test here touches the network. ``FmaSource`` downloads are exercised
through ``httpx.MockTransport``, which answers requests entirely in-process —
useful for pinning the resume/verify logic without waiting on a 7.2 GiB
transfer or depending on a remote server being up.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from shazam.sources import TrackMeta
from shazam.sources.fma import FmaSource, FmaSourceError, _download_resumable, _sha1_of, track_path
from shazam.sources.local import LocalSource

TRACKS_CSV = """\
,artist,track,track
,name,title,genre_top
track_id,,,
2,DJ Test,First Track,Hip-Hop
3,,Second Track,
5,Some Artist,,Rock
7,Other Artist,No Audio Track,Jazz
"""


class TestTrackPath:
    def test_single_digit_id_zero_pads_to_six_digits(self) -> None:
        assert track_path(Path("fma_small"), 2) == Path("fma_small/000/000002.mp3")

    def test_bucket_is_the_first_three_digits_of_the_padded_id(self) -> None:
        assert track_path(Path("fma_small"), 154308) == Path("fma_small/154/154308.mp3")

    def test_root_is_respected_regardless_of_shape(self) -> None:
        assert track_path(Path("/data/fma/fma_small"), 99) == Path(
            "/data/fma/fma_small/000/000099.mp3"
        )


class TestLocalSource:
    def test_scans_recursively_and_uses_filename_as_title(self, tmp_path: Path) -> None:
        (tmp_path / "sub").mkdir()
        (tmp_path / "a.mp3").write_bytes(b"fake")
        (tmp_path / "sub" / "b.wav").write_bytes(b"fake")
        (tmp_path / "not-audio.txt").write_text("ignore me")

        tracks = sorted(LocalSource().tracks(tmp_path), key=lambda t: t.title)

        assert [t.title for t in tracks] == ["a", "b"]
        assert all(t.source == "local" for t in tracks)
        assert all(t.artist is None for t in tracks)
        # Absolute: a build's resume logic keys on this exact string matching
        # what got written to the database on the previous run.
        assert all(t.path.is_absolute() for t in tracks)

    def test_missing_directory_yields_nothing(self, tmp_path: Path) -> None:
        assert list(LocalSource().tracks(tmp_path / "does-not-exist")) == []

    def test_non_audio_extensions_are_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "cover.jpg").write_bytes(b"fake")
        (tmp_path / "readme.md").write_text("hello")

        assert list(LocalSource().tracks(tmp_path)) == []


class TestFmaSourceTracks:
    def _catalogue_root(self, tmp_path: Path) -> Path:
        """Build a tmp_path tree shaped like a fetched-and-extracted FMA catalogue."""
        metadata_dir = tmp_path / "fma_metadata"
        metadata_dir.mkdir()
        (metadata_dir / "tracks.csv").write_text(TRACKS_CSV)

        audio_dir = tmp_path / "fma_small" / "000"
        audio_dir.mkdir(parents=True)
        # Track 7 has metadata but no audio file — must be skipped, not crash.
        (audio_dir / "000002.mp3").write_bytes(b"fake mp3")
        (audio_dir / "000003.mp3").write_bytes(b"fake mp3")

        return tmp_path

    def test_yields_only_tracks_with_both_audio_and_a_title(self, tmp_path: Path) -> None:
        root = self._catalogue_root(tmp_path)

        tracks = sorted(FmaSource().tracks(root), key=lambda t: t.title)

        assert [t.title for t in tracks] == ["First Track", "Second Track"]

    def test_metadata_is_carried_through_and_paths_resolved(self, tmp_path: Path) -> None:
        root = self._catalogue_root(tmp_path)

        by_title = {t.title: t for t in FmaSource().tracks(root)}

        first = by_title["First Track"]
        assert first.artist == "DJ Test"
        assert first.genre == "Hip-Hop"
        assert first.source == "fma"
        assert first.path == (root / "fma_small" / "000" / "000002.mp3").resolve()

    def test_blank_artist_and_genre_become_none_not_empty_string(self, tmp_path: Path) -> None:
        root = self._catalogue_root(tmp_path)

        second = next(t for t in FmaSource().tracks(root) if t.title == "Second Track")

        assert second.artist is None
        assert second.genre is None

    def test_track_with_metadata_but_no_audio_file_is_skipped(self, tmp_path: Path) -> None:
        root = self._catalogue_root(tmp_path)

        titles = [t.title for t in FmaSource().tracks(root)]

        assert "No Audio Track" not in titles

    def test_track_with_audio_but_no_title_is_skipped(self, tmp_path: Path) -> None:
        root = self._catalogue_root(tmp_path)
        (root / "fma_small" / "000" / "000005.mp3").write_bytes(b"fake mp3")

        titles = [t.title for t in FmaSource().tracks(root)]

        assert titles == ["First Track", "Second Track"]

    def test_missing_metadata_csv_yields_nothing(self, tmp_path: Path) -> None:
        assert list(FmaSource().tracks(tmp_path)) == []

    def test_source_satisfies_the_music_source_protocol(self, tmp_path: Path) -> None:
        from shazam.sources import MusicSource

        assert isinstance(FmaSource(), MusicSource)


class TestDownloadResume:
    """Pure resume/verify logic, exercised without any real network access."""

    def test_fresh_download_writes_the_full_body_and_verifies(self, tmp_path: Path) -> None:
        content = b"a" * 256
        expected_sha1 = hashlib.sha1(content).hexdigest()
        path = tmp_path / "archive.bin"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "HEAD":
                return httpx.Response(200, headers={"content-length": str(len(content))})
            assert "range" not in request.headers
            return httpx.Response(200, content=content)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        _download_resumable(client, "https://example.test/archive.bin", path, expected_sha1)

        assert path.read_bytes() == content

    def test_partial_local_file_resumes_with_a_range_header(self, tmp_path: Path) -> None:
        content = b"b" * 300
        already_have = content[:120]
        rest = content[120:]
        expected_sha1 = hashlib.sha1(content).hexdigest()

        path = tmp_path / "archive.bin"
        path.write_bytes(already_have)

        seen_ranges: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "HEAD":
                return httpx.Response(200, headers={"content-length": str(len(content))})
            seen_ranges.append(request.headers.get("range"))
            return httpx.Response(206, content=rest)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        _download_resumable(client, "https://example.test/archive.bin", path, expected_sha1)

        assert seen_ranges == ["bytes=120-"]
        assert path.read_bytes() == content

    def test_complete_and_valid_file_skips_the_download_entirely(self, tmp_path: Path) -> None:
        content = b"c" * 64
        expected_sha1 = hashlib.sha1(content).hexdigest()
        path = tmp_path / "archive.bin"
        path.write_bytes(content)

        get_requests = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal get_requests
            if request.method == "HEAD":
                return httpx.Response(200, headers={"content-length": str(len(content))})
            get_requests += 1
            raise AssertionError("should not re-download a complete, checksum-valid file")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        _download_resumable(client, "https://example.test/archive.bin", path, expected_sha1)

        assert get_requests == 0

    def test_server_ignoring_range_falls_back_to_a_full_fresh_write(self, tmp_path: Path) -> None:
        """A 200 in reply to a Range request means: start over, don't append."""
        content = b"d" * 200
        expected_sha1 = hashlib.sha1(content).hexdigest()

        path = tmp_path / "archive.bin"
        path.write_bytes(b"stale-partial-data")

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "HEAD":
                return httpx.Response(200, headers={"content-length": str(len(content))})
            return httpx.Response(200, content=content)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        _download_resumable(client, "https://example.test/archive.bin", path, expected_sha1)

        assert path.read_bytes() == content

    def test_checksum_mismatch_deletes_the_file_and_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "archive.bin"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "HEAD":
                return httpx.Response(200, headers={"content-length": "4"})
            return httpx.Response(200, content=b"nope")

        client = httpx.Client(transport=httpx.MockTransport(handler))

        with pytest.raises(FmaSourceError, match="SHA1 mismatch"):
            _download_resumable(
                client, "https://example.test/archive.bin", path, "0" * 40
            )

        assert not path.exists(), "corrupt data must not be left on disk"


def test_sha1_of_matches_hashlib_reference(tmp_path: Path) -> None:
    path = tmp_path / "sample.bin"
    data = b"the quick brown fox" * 1000
    path.write_bytes(data)

    assert _sha1_of(path) == hashlib.sha1(data).hexdigest()


def test_track_meta_is_frozen() -> None:
    meta = TrackMeta(title="T", artist=None, path=Path("x.mp3"), source="local")
    with pytest.raises(AttributeError):
        meta.title = "changed"  # type: ignore[misc]
