"""Integration tests against a real PostgreSQL instance.

Skipped when no database is reachable, so the unit suite still runs anywhere.
Start one with ``docker compose up -d db``.

The overflow test here is the one that matters. Design decision #4 says the
``hash`` column must be ``BIGINT``, and the failure mode of getting it wrong is
nasty: only hashes with a high anchor frequency exceed the signed ``INTEGER``
range, so the corpus would be corrupted for high-frequency content alone and
look perfectly healthy everywhere else.
"""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
import pytest

from shazam.config import DspConfig
from shazam.database import (
    SongRecord,
    copy_fingerprints,
    existing_paths,
    fetch_song,
    init_schema,
    insert_song,
    lookup,
)
from shazam.hashing import HASH_BITS, pack_hash

MAX_HASH = 2**HASH_BITS - 1


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    """A connection to a scratch schema, dropped afterwards."""
    try:
        connection = psycopg.connect(
            "postgresql://shazam:shazam@localhost:5432/shazam", connect_timeout=3
        )
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL not reachable — run `docker compose up -d db`")

    with connection.cursor() as cursor:
        cursor.execute("CREATE SCHEMA IF NOT EXISTS pytest_scratch")
        cursor.execute("SET search_path TO pytest_scratch")
    connection.commit()
    init_schema(connection)

    yield connection

    with connection.cursor() as cursor:
        cursor.execute("DROP SCHEMA pytest_scratch CASCADE")
    connection.commit()
    connection.close()


def _add_song(conn: psycopg.Connection, path: str = "/tmp/track.wav") -> int:
    song_id = insert_song(conn, SongRecord("Title", "Artist", path, 30.0, "test"))
    assert song_id is not None
    return song_id


def test_hash_column_is_bigint(conn: psycopg.Connection) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT data_type FROM information_schema.columns
            WHERE table_name = 'fingerprints' AND column_name = 'hash'
              AND table_schema = 'pytest_scratch'
            """
        )
        row = cursor.fetchone()

    assert row is not None
    assert row[0] == "bigint"


def test_maximum_hash_survives_copy_without_overflow(conn: psycopg.Connection) -> None:
    """The widest possible hash must come back byte-for-byte identical."""
    song_id = _add_song(conn)
    assert MAX_HASH == 4294967295 > 2147483647

    copy_fingerprints(conn, song_id, [(MAX_HASH, 0)])
    conn.commit()

    assert lookup(conn, [MAX_HASH]) == [(MAX_HASH, song_id, 0)]


def test_hashes_reachable_from_real_audio_can_exceed_integer(conn: psycopg.Connection) -> None:
    """The narrow, nasty case design decision #4 protects against.

    With a 1024-point window there are 513 bins, so an anchor bin occupies at
    most 512. Since the anchor is shifted left by 22, only bin 512 pushes a
    hash past the signed INTEGER ceiling: 512 << 22 is 2147483648, exactly one
    above it, while bin 511 stays comfortably below.

    That makes the consequence of using INTEGER worse than a plain overflow.
    It would corrupt only fingerprints anchored on the single topmost frequency
    bin — rare, spectrum-dependent, and invisible in the other 99.8% of the
    data.
    """
    config = DspConfig()
    top_bin = config.n_bins - 1
    assert top_bin == 512

    assert pack_hash(top_bin - 1, 300, 40) < 2147483647, "bin 511 fits in INTEGER"
    assert pack_hash(top_bin, 300, 40) > 2147483647, "bin 512 does not"

    song_id = _add_song(conn)
    max_delta = config.seconds_to_frames(config.max_time_delta)
    hashes = [
        pack_hash(top_bin, target, delta)
        for target, delta in ((0, 1), (300, 40), (top_bin, max_delta))
    ]

    copy_fingerprints(conn, song_id, [(h, index) for index, h in enumerate(hashes)])
    conn.commit()

    assert sorted(h for h, _, _ in lookup(conn, hashes)) == sorted(hashes)


def test_copy_reports_row_count_and_lookup_returns_them(conn: psycopg.Connection) -> None:
    song_id = _add_song(conn)
    pairs = [(pack_hash(i, i + 1, 10), i) for i in range(100)]

    written = copy_fingerprints(conn, song_id, pairs)
    conn.commit()

    assert written == 100
    assert len(lookup(conn, [h for h, _ in pairs])) == 100


def test_lookup_of_absent_hashes_returns_nothing(conn: psycopg.Connection) -> None:
    _add_song(conn)
    assert lookup(conn, [12345, 67890]) == []
    assert lookup(conn, []) == []


def test_duplicate_path_is_not_inserted_twice(conn: psycopg.Connection) -> None:
    """What makes an interrupted build safe to resume."""
    first = insert_song(conn, SongRecord("A", None, "/tmp/same.wav", 1.0, "test"))
    second = insert_song(conn, SongRecord("A again", None, "/tmp/same.wav", 1.0, "test"))
    conn.commit()

    assert first is not None
    assert second is None


def test_existing_paths_reports_what_was_built(conn: psycopg.Connection) -> None:
    _add_song(conn, "/tmp/one.wav")
    _add_song(conn, "/tmp/two.wav")
    conn.commit()

    assert existing_paths(conn) == {"/tmp/one.wav", "/tmp/two.wav"}


def test_fetch_song_returns_metadata(conn: psycopg.Connection) -> None:
    song_id = _add_song(conn)
    conn.commit()

    assert fetch_song(conn, song_id) == ("Title", "Artist")
    assert fetch_song(conn, song_id + 9999) is None
