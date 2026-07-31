"""PostgreSQL storage for fingerprints: schema, bulk load, and lookup.

Scale drives every choice here. Eight thousand tracks produce roughly 48 million
fingerprint rows, which is the difference between "any approach works" and
"only the right one finishes":

* Rows are loaded with binary ``COPY``, never ``executemany``. Each row through
  ``executemany`` costs a parse and a round trip; ``COPY`` streams bytes
  straight into the server and runs orders of magnitude faster.
* The hash index is built *after* loading. Maintaining a b-tree across 48
  million inserts is far slower than sorting once at the end.
* ``hash`` is ``BIGINT``. See :data:`HASH_COLUMN_TYPE`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import psycopg
from psycopg_pool import ConnectionPool

from shazam.config import DatabaseSettings

# Design decision #4. The widest hash is 4294967295, and PostgreSQL's INTEGER
# is signed with a ceiling of 2147483647. INTEGER would therefore overflow on
# exactly those hashes whose anchor frequency is high — the top of the spectrum
# — corrupting part of the data while the rest stayed correct. A partial,
# frequency-dependent corruption is close to impossible to trace back.
HASH_COLUMN_TYPE = "BIGINT"

SCHEMA = """
CREATE TABLE IF NOT EXISTS songs (
    id       SERIAL PRIMARY KEY,
    title    TEXT NOT NULL,
    artist   TEXT,
    path     TEXT NOT NULL UNIQUE,
    duration REAL,
    source   TEXT NOT NULL DEFAULT 'local'
);

CREATE TABLE IF NOT EXISTS fingerprints (
    hash     BIGINT  NOT NULL,
    song_id  INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
    "offset" INTEGER NOT NULL
);
"""


@dataclass(frozen=True)
class SongRecord:
    """Catalogue metadata for one track."""

    title: str
    artist: str | None
    path: str
    duration: float | None
    source: str = "local"


@dataclass(frozen=True)
class DatabaseStats:
    """A snapshot of corpus size, used by ``shazam stats`` and ``/api/health``."""

    songs: int
    fingerprints: int
    distinct_hashes: int
    most_common: list[tuple[int, int]]


def connect(settings: DatabaseSettings | None = None) -> psycopg.Connection:
    """Open a single connection. Callers are responsible for closing it."""
    settings = settings or DatabaseSettings()
    return psycopg.connect(settings.database_url)


def create_pool(settings: DatabaseSettings | None = None) -> ConnectionPool:
    """Create a connection pool for the HTTP server.

    The API handles concurrent requests and must not pay connection setup on
    each one, nor open an unbounded number of them.
    """
    settings = settings or DatabaseSettings()
    return ConnectionPool(
        settings.database_url,
        min_size=settings.pool_min_size,
        max_size=settings.pool_max_size,
        open=False,
    )


def init_schema(conn: psycopg.Connection) -> None:
    """Create the tables. Deliberately does not create the hash index."""
    with conn.cursor() as cursor:
        cursor.execute(SCHEMA)
    conn.commit()


def create_index(conn: psycopg.Connection) -> None:
    """Build the hash index. Run once, after the corpus is fully loaded.

    ``maintenance_work_mem`` is raised for the duration because sorting 48
    million keys in a small work area spills to disk and takes far longer.
    """
    with conn.cursor() as cursor:
        cursor.execute("SET maintenance_work_mem = '1GB'")
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_fingerprints_hash ON fingerprints USING btree (hash)'
        )
    conn.commit()


def drop_index(conn: psycopg.Connection) -> None:
    """Drop the hash index so a bulk load is not slowed by maintaining it."""
    with conn.cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS idx_fingerprints_hash")
    conn.commit()


def insert_song(conn: psycopg.Connection, song: SongRecord) -> int | None:
    """Insert a track, or return ``None`` if its path is already catalogued.

    The path uniqueness check is what makes ``shazam build`` resumable: an
    interrupted build can be restarted without duplicating what it already did.
    """
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO songs (title, artist, path, duration, source)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (path) DO NOTHING
            RETURNING id
            """,
            (song.title, song.artist, song.path, song.duration, song.source),
        )
        row = cursor.fetchone()
    return int(row[0]) if row else None


def copy_fingerprints(
    conn: psycopg.Connection,
    song_id: int,
    pairs: Iterable[tuple[int, int]],
) -> int:
    """Bulk-load one track's fingerprints with binary ``COPY``.

    Args:
        conn: Open connection.
        song_id: Track these fingerprints belong to.
        pairs: ``(hash, anchor_frame)`` from :func:`~shazam.hashing.generate_hashes`.

    Returns:
        How many rows were written.
    """
    written = 0
    with conn.cursor() as cursor, cursor.copy(
        'COPY fingerprints (hash, song_id, "offset") FROM STDIN BINARY'
    ) as copy:
        # Declared explicitly rather than inferred. int8 for the hash is the
        # wire-level half of design decision #4; letting psycopg guess from
        # the first row would pick int4 for any track whose first hash
        # happened to be small.
        copy.set_types(["int8", "int4", "int4"])
        for hash_value, offset in pairs:
            copy.write_row((hash_value, song_id, offset))
            written += 1
    return written


def lookup(conn: psycopg.Connection, hashes: Sequence[int]) -> list[tuple[int, int, int]]:
    """Fetch every stored fingerprint matching any of ``hashes``.

    Uses ``= ANY(array)`` rather than a generated ``IN (...)`` list: one
    prepared statement regardless of query size, no string building, and no
    parameter-count ceiling.

    Returns:
        ``(hash, song_id, offset)`` rows, ready for
        :func:`~shazam.matcher.rank_candidates`.
    """
    if not hashes:
        return []

    with conn.cursor() as cursor:
        cursor.execute(
            'SELECT hash, song_id, "offset" FROM fingerprints WHERE hash = ANY(%s::bigint[])',
            (list(hashes),),
        )
        return [(int(h), int(song_id), int(offset)) for h, song_id, offset in cursor.fetchall()]


def fetch_song(conn: psycopg.Connection, song_id: int) -> tuple[str, str | None] | None:
    """Return ``(title, artist)`` for a track, or ``None`` if it is unknown."""
    with conn.cursor() as cursor:
        cursor.execute("SELECT title, artist FROM songs WHERE id = %s", (song_id,))
        row = cursor.fetchone()
    return (str(row[0]), row[1]) if row else None


def existing_paths(conn: psycopg.Connection) -> set[str]:
    """Every catalogued path, so a resumed build can skip finished work."""
    with conn.cursor() as cursor:
        cursor.execute("SELECT path FROM songs")
        return {str(row[0]) for row in cursor.fetchall()}


def stats(conn: psycopg.Connection, top: int = 10) -> DatabaseStats:
    """Summarise the corpus, including the most frequent hashes.

    The frequency tail is not decoration. A hash shared by thousands of tracks
    carries almost no discriminating information while costing the most to look
    up, so this is the measurement that says whether such hashes need pruning.
    """
    with conn.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM songs")
        songs = int((cursor.fetchone() or [0])[0])

        cursor.execute("SELECT COUNT(*) FROM fingerprints")
        fingerprints = int((cursor.fetchone() or [0])[0])

        cursor.execute("SELECT COUNT(DISTINCT hash) FROM fingerprints")
        distinct = int((cursor.fetchone() or [0])[0])

        cursor.execute(
            """
            SELECT hash, COUNT(*) AS occurrences
            FROM fingerprints
            GROUP BY hash
            ORDER BY occurrences DESC
            LIMIT %s
            """,
            (top,),
        )
        most_common = [(int(h), int(count)) for h, count in cursor.fetchall()]

    return DatabaseStats(songs, fingerprints, distinct, most_common)
