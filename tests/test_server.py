"""Integration tests for the HTTP API, against a real PostgreSQL instance.

Skipped when no database is reachable, matching the pattern in
``tests/test_database.py``. Unlike that file's scratch schema, these tests
read the corpus already loaded for development (`docker compose up -d db` +
`shazam build`) — a match test needs real fingerprints to match against.

`DATABASE_URL` must be set before `server.app` is imported, since
`server.settings.Settings` validates it eagerly at construction. `setdefault`
keeps this file safe to run standalone while still respecting a value the
environment already provides.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql://shazam:shazam@localhost:5432/shazam")

import io
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import psycopg
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from server.app import SchemaMissingError, create_app

QUERIES_DIR = Path(__file__).resolve().parent.parent / "data" / "queries"
DATABASE_URL = os.environ["DATABASE_URL"]


def _database_reachable() -> bool:
    try:
        psycopg.connect(DATABASE_URL, connect_timeout=3).close()
    except psycopg.OperationalError:
        return False
    return True


def _silence_wav_bytes(seconds: float, sample_rate: int = 11025) -> bytes:
    """A minimal, valid WAV shorter than the one-second query floor."""
    samples = np.zeros(int(seconds * sample_rate), dtype=np.float32)
    buffer = io.BytesIO()
    sf.write(buffer, samples, sample_rate, format="WAV")
    return buffer.getvalue()


@pytest.fixture
def client() -> Iterator[TestClient]:
    if not _database_reachable():
        pytest.skip("PostgreSQL not reachable — run `docker compose up -d db`")
    try:
        with TestClient(create_app()) as test_client:
            yield test_client
    except SchemaMissingError as exc:
        pytest.skip(f"Corpus not built: {exc}")


def _upload(path: Path) -> dict[str, tuple[str, bytes, str]]:
    return {"file": (path.name, path.read_bytes(), "audio/wav")}


def test_health_reports_cached_counts(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "connected"
    assert body["songs"] >= 1
    assert body["fingerprints"] >= 1


def test_match_identifies_known_query(client: TestClient) -> None:
    response = client.post("/api/match", files=_upload(QUERIES_DIR / "q-10s.wav"))
    assert response.status_code == 200

    body = response.json()
    assert body["match"] is not None
    assert body["match"]["title"] == "02-d-minor"
    assert body["match"]["strength"] in ("strong", "moderate", "weak")
    assert body["match"]["offsetSeconds"] == pytest.approx(12.0, abs=1.0)
    assert body["queryHashes"] > 0
    assert body["elapsedMs"] >= 0


def test_match_of_track_outside_corpus_is_an_honest_null(client: TestClient) -> None:
    response = client.post("/api/match", files=_upload(QUERIES_DIR / "q-notincorpus.wav"))
    assert response.status_code == 200

    body = response.json()
    assert body["match"] is None
    assert body["queryHashes"] > 0


def test_match_of_noise_is_an_honest_null(client: TestClient) -> None:
    response = client.post("/api/match", files=_upload(QUERIES_DIR / "q-noise.wav"))
    assert response.status_code == 200
    assert response.json()["match"] is None


def test_corrupt_audio_returns_422(client: TestClient) -> None:
    garbage = {"file": ("clip.wav", b"this is not audio data" * 50, "audio/wav")}
    response = client.post("/api/match", files=garbage)

    assert response.status_code == 422
    assert response.json()["detail"]


def test_audio_under_one_second_returns_422(client: TestClient) -> None:
    too_short = {"file": ("clip.wav", _silence_wav_bytes(0.5), "audio/wav")}
    response = client.post("/api/match", files=too_short)

    assert response.status_code == 422
    assert "ngắn" in response.json()["detail"]


def test_oversized_upload_is_rejected(client: TestClient) -> None:
    oversized = b"\x00" * (10 * 1024 * 1024 + 1024)
    response = client.post("/api/match", files={"file": ("big.wav", oversized, "audio/wav")})

    assert response.status_code == 413


def test_songs_pagination(client: TestClient) -> None:
    response = client.get("/api/songs", params={"limit": 2, "offset": 0})
    assert response.status_code == 200

    body = response.json()
    assert len(body["items"]) <= 2
    assert body["limit"] == 2
    assert body["offset"] == 0
    assert body["total"] >= len(body["items"])


def test_songs_search_by_title(client: TestClient) -> None:
    response = client.get("/api/songs", params={"q": "d-minor"})
    assert response.status_code == 200

    body = response.json()
    assert body["total"] >= 1
    assert all("d-minor" in item["title"] for item in body["items"])


def test_songs_search_with_no_match_returns_empty(client: TestClient) -> None:
    response = client.get("/api/songs", params={"q": "no-such-track-xyz"})
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}


def test_spectrogram_returns_png(client: TestClient) -> None:
    response = client.post("/api/spectrogram", files=_upload(QUERIES_DIR / "q-10s.wav"))
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")
