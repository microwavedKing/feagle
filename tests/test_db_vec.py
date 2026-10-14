"""Tests for the sqlite-vec vector table management."""

from __future__ import annotations

import sqlite3

import pytest
import sqlite_vec

from feagle import db


@pytest.fixture
def conn(tmp_path):
    connection = db.open_database(tmp_path / "vec.sqlite")
    yield connection
    connection.close()


def test_sqlite_vec_is_loaded(conn):
    version = conn.execute("SELECT vec_version()").fetchone()[0]
    assert version.startswith("v")


def test_no_vec_table_initially(conn):
    assert db.has_vec_table(conn) is False
    assert db.get_embedding_dim(conn) == 0


def test_ensure_vec_table_creates_and_records(conn):
    db.ensure_vec_table(conn, 768, model_id="embeddinggemma-2-q8")
    assert db.has_vec_table(conn) is True
    assert db.get_embedding_dim(conn) == 768
    assert db.get_meta(conn, "model_id") == "embeddinggemma-2-q8"


def test_ensure_vec_table_is_idempotent(conn):
    db.ensure_vec_table(conn, 256)
    db.ensure_vec_table(conn, 256)
    assert db.get_embedding_dim(conn) == 256


def test_dim_mismatch_raises(conn):
    db.ensure_vec_table(conn, 768)
    with pytest.raises(db.DimMismatchError, match="--rebuild"):
        db.ensure_vec_table(conn, 256)


def test_non_positive_dim_rejected(conn):
    with pytest.raises(ValueError):
        db.ensure_vec_table(conn, 0)


def test_drop_vec_table(conn):
    db.ensure_vec_table(conn, 128)
    db.drop_vec_table(conn)
    assert db.has_vec_table(conn) is False
    assert db.get_embedding_dim(conn) == 0


def test_insert_and_knn(conn):
    db.ensure_vec_table(conn, 3)
    rows = [(1, [1.0, 0.0, 0.0]), (2, [0.0, 1.0, 0.0]), (3, [0.9, 0.1, 0.0])]
    for chunk_id, vector in rows:
        conn.execute(
            "INSERT INTO chunks_vec(chunk_id, embedding) VALUES (?, ?)",
            (chunk_id, sqlite_vec.serialize_float32(vector)),
        )
    conn.commit()

    hits = conn.execute(
        "SELECT chunk_id, distance FROM chunks_vec WHERE embedding MATCH ? AND k = 2 "
        "ORDER BY distance",
        (sqlite_vec.serialize_float32([1.0, 0.0, 0.0]),),
    ).fetchall()
    assert [h["chunk_id"] for h in hits] == [1, 3]


def test_schema_version_unchanged_by_vec(conn):
    assert db.get_schema_version(conn) == db.SCHEMA_VERSION == 1


def test_missing_meta_table_returns_zero(tmp_path):
    plain = sqlite3.connect(tmp_path / "plain.sqlite")
    plain.execute("CREATE TABLE other(x)")
    assert db.get_embedding_dim(plain) == 0
    plain.close()
