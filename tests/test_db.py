"""Tests for the database layer."""

from __future__ import annotations

import sqlite3
import stat
import time

import pytest

from feagle import db


@pytest.fixture
def db_file(tmp_path):
    return tmp_path / "test.sqlite"


def test_open_database_creates_schema(db_file):
    conn = db.open_database(db_file)
    try:
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
        }
        assert {"meta", "files", "chunks", "chunks_fts"} <= tables
        assert db.get_schema_version(conn) == db.SCHEMA_VERSION
        assert db.get_meta(conn, "created_at") is not None
    finally:
        conn.close()


def test_migrate_is_idempotent(db_file):
    conn = db.open_database(db_file)
    try:
        created = db.get_meta(conn, "created_at")
        assert db.migrate(conn) == db.SCHEMA_VERSION
        assert db.migrate(conn) == db.SCHEMA_VERSION
        assert db.get_meta(conn, "created_at") == created
    finally:
        conn.close()


def test_newer_schema_is_rejected(db_file):
    conn = db.open_database(db_file)
    db.set_meta(conn, "schema_version", str(db.SCHEMA_VERSION + 1))
    conn.commit()
    with pytest.raises(RuntimeError, match="newer than supported"):
        db.migrate(conn)
    conn.close()


def test_database_file_is_private(db_file):
    conn = db.open_database(db_file)
    conn.close()
    assert stat.S_IMODE(db_file.stat().st_mode) == 0o600


def test_read_only_connection_blocks_writes(db_file):
    conn = db.open_database(db_file)
    conn.close()

    ro = db.connect(db_file, read_only=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("INSERT INTO meta(key, value) VALUES('x', '1')")
    finally:
        ro.close()


def test_read_only_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        db.connect(tmp_path / "nope.sqlite", read_only=True)


def test_foreign_key_cascade(db_file):
    conn = db.open_database(db_file)
    try:
        now = int(time.time())
        cur = conn.execute(
            "INSERT INTO files(path, size, mtime_ns, updated_at) VALUES(?,?,?,?)",
            ("/tmp/a.txt", 10, 1, now),
        )
        file_id = cur.lastrowid
        conn.execute(
            "INSERT INTO chunks(file_id, seq, line, body) VALUES(?,?,?,?)",
            (file_id, 0, 1, "hello world"),
        )
        conn.commit()
        conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
        conn.commit()
        assert conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"] == 0
    finally:
        conn.close()


def test_chunks_file_seq_is_unique(db_file):
    conn = db.open_database(db_file)
    try:
        now = int(time.time())
        cur = conn.execute(
            "INSERT INTO files(path, size, mtime_ns, updated_at) VALUES(?,?,?,?)",
            ("/tmp/a.txt", 10, 1, now),
        )
        file_id = cur.lastrowid
        conn.execute(
            "INSERT INTO chunks(file_id, seq, body) VALUES(?,?,?)", (file_id, 0, "one")
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO chunks(file_id, seq, body) VALUES(?,?,?)", (file_id, 0, "two")
            )
    finally:
        conn.close()


def test_fts_index_roundtrip(db_file):
    conn = db.open_database(db_file)
    try:
        conn.execute(
            "INSERT INTO chunks_fts(body, stem, chunk_id) VALUES(?,?,?)",
            ("quick brown fox", "quick brown fox", 1),
        )
        conn.execute(
            "INSERT INTO chunks_fts(body, stem, chunk_id) VALUES(?,?,?)",
            ("lazy dog", "lazy dog", 2),
        )
        conn.commit()
        rows = conn.execute(
            "SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts)",
            ('body:"brown"',),
        ).fetchall()
        assert [r["chunk_id"] for r in rows] == [1]
    finally:
        conn.close()


def test_migration_v1_to_v2_adds_stem_column(tmp_path):
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE files(
          id INTEGER PRIMARY KEY, path TEXT UNIQUE, size INTEGER, mtime_ns INTEGER,
          hash TEXT, state TEXT DEFAULT 'ok', error TEXT, embedded INTEGER DEFAULT 0,
          updated_at INTEGER);
        CREATE TABLE chunks(
          id INTEGER PRIMARY KEY, file_id INTEGER, seq INTEGER, page INTEGER,
          line INTEGER, body TEXT);
        CREATE VIRTUAL TABLE chunks_fts USING fts5(
          body, chunk_id UNINDEXED, tokenize = 'unicode61 remove_diacritics 2');
        INSERT INTO meta(key, value) VALUES('schema_version', '1');
        INSERT INTO files(id, path, size, mtime_ns, updated_at) VALUES(1, '/x.txt', 10, 1, 1);
        INSERT INTO chunks(id, file_id, seq, body) VALUES(1, 1, 0, 'старая книга лежит');
        INSERT INTO chunks_fts(body, chunk_id) VALUES('старая книга лежит', 1);
        """
    )
    conn.commit()
    conn.close()

    migrated = db.connect(path)
    try:
        assert db.migrate(migrated) == 2
        rows = migrated.execute(
            "SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH ?", ('stem:"книг"',)
        ).fetchall()
        assert [r["chunk_id"] for r in rows] == [1]
    finally:
        migrated.close()
