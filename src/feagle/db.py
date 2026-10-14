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
#: The ``chunks_vec`` table is managed separately (see ``ensure_vec_table``)
#: because its dimension is fixed at CREATE time and depends on the model.
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


class DimMismatchError(RuntimeError):
    """The stored vector dimension differs from the requested one."""


def load_sqlite_vec(conn: sqlite3.Connection) -> bool:
    """Load the sqlite-vec extension into ``conn``. Returns success."""
    try:
        import sqlite_vec
    except ImportError:
        log.warning("sqlite-vec is not installed; semantic search disabled")
        return False
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
    except (sqlite3.Error, AttributeError) as exc:
        log.warning("cannot load sqlite-vec: %s", exc)
        return False
    finally:
        with contextlib.suppress(sqlite3.Error):
            conn.enable_load_extension(False)
    return True


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
    load_sqlite_vec(conn)
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


# -- vector table (sqlite-vec / vec0) --------------------------------------


def get_embedding_dim(conn: sqlite3.Connection) -> int:
    """Return the stored vector dimension, or 0 when not configured yet."""
    try:
        value = get_meta(conn, "embedding_dim")
    except sqlite3.OperationalError:
        return 0
    return int(value) if value else 0


def has_vec_table(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chunks_vec'"
    ).fetchone()
    return row is not None


def ensure_vec_table(
    conn: sqlite3.Connection, dim: int, *, model_id: str | None = None
) -> None:
    """Create ``chunks_vec`` for ``dim`` dimensions, or check compatibility.

    Raises :class:`DimMismatchError` when the database already holds vectors of
    a different dimension. Requirement (sqlite-vec, RESEARCH §4.2): query and
    document vectors must share the exact same dimension.
    """
    if dim <= 0:
        raise ValueError(f"embedding dimension must be positive, got {dim}")

    stored = get_embedding_dim(conn)
    if stored and stored != dim:
        raise DimMismatchError(
            f"index holds {stored}-dimensional vectors but the model produces {dim}; "
            "run 'feagle index --rebuild' to re-embed everything"
        )

    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0("
        f"chunk_id INTEGER PRIMARY KEY, embedding float[{dim}])"
    )
    set_meta(conn, "embedding_dim", str(dim))
    if model_id is not None:
        set_meta(conn, "model_id", model_id)
    conn.commit()


def drop_vec_table(conn: sqlite3.Connection) -> None:
    """Remove the vector table and forget the stored dimension."""
    conn.execute("DROP TABLE IF EXISTS chunks_vec")
    set_meta(conn, "embedding_dim", "0")
    conn.commit()
