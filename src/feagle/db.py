"""SQLite storage: connections, schema and migrations.

One SQLite file holds everything: plain tables, an FTS5 index and (from a later
phase) a ``vec0`` virtual table. Writers use WAL; readers open the file
read-only with ``query_only`` set.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import time
from pathlib import Path

from . import paths
from .log import get_logger

log = get_logger("db")

#: Current on-disk schema version. Bump together with a migration step.
SCHEMA_VERSION = 1

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta(
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files(
  id          INTEGER PRIMARY KEY,
  path        TEXT NOT NULL UNIQUE,
  size        INTEGER NOT NULL,
  mtime_ns    INTEGER NOT NULL,
  hash        TEXT,
  state       TEXT NOT NULL DEFAULT 'ok',
  error       TEXT,
  embedded    INTEGER NOT NULL DEFAULT 0,
  updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks(
  id      INTEGER PRIMARY KEY,
  file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  seq     INTEGER NOT NULL,
  page    INTEGER,
  line    INTEGER,
  body    TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS chunks_file_seq ON chunks(file_id, seq);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  body, chunk_id UNINDEXED,
  tokenize = 'unicode61 remove_diacritics 2'
);
"""


def connect(path: Path | str | None = None, *, read_only: bool = False) -> sqlite3.Connection:
    """Open the database, applying sane pragmas.

    A writable connection creates the parent directory (0700) and the file
    itself (0600). A read-only connection never creates anything.
    """
    path = Path(path) if path is not None else paths.db_path()

    if read_only:
        if not path.exists():
            raise FileNotFoundError(f"database not found: {path}")
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(path.parent, 0o700)
        conn = sqlite3.connect(path)
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)

    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    if read_only:
        conn.execute("PRAGMA query_only = ON")
    else:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Return the stored schema version, or 0 when the database is empty."""
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row["value"]) if row is not None else 0


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row is not None else None


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all base objects and seed ``meta`` on a fresh database."""
    conn.executescript(_SCHEMA_SQL)
    if get_schema_version(conn) == 0:
        set_meta(conn, "schema_version", str(SCHEMA_VERSION))
        set_meta(conn, "created_at", str(int(time.time())))


def migrate(conn: sqlite3.Connection) -> int:
    """Bring the database up to :data:`SCHEMA_VERSION` and return the version.

    Raises if the file was written by a newer feagle (downgrade is unsupported).
    """
    version = get_schema_version(conn)
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"database schema v{version} is newer than supported v{SCHEMA_VERSION}; "
            "please upgrade feagle"
        )
    if version < SCHEMA_VERSION:
        # v0 -> v1: initial schema. Future steps are added here, one per version.
        init_schema(conn)
        version = SCHEMA_VERSION
    conn.commit()
    return version


def open_database(
    path: Path | str | None = None, *, read_only: bool = False
) -> sqlite3.Connection:
    """Open a database and ensure the schema is current (writable connections)."""
    conn = connect(path, read_only=read_only)
    if not read_only:
        migrate(conn)
    return conn
