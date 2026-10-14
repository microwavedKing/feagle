"""Tests for vector search."""

from __future__ import annotations

import sqlite3

import pytest

from feagle import db
from feagle.search import vec


@pytest.fixture
def conn(tmp_path):
    connection = db.open_database(tmp_path / "vec.sqlite")
    db.ensure_vec_table(connection, 3)
    yield connection
    connection.close()


def test_knn_orders_by_distance(conn):
    vec.insert_vectors(
        conn,
        [(10, [1.0, 0.0, 0.0]), (20, [0.0, 1.0, 0.0]), (30, [0.95, 0.05, 0.0])],
    )
    conn.commit()
    hits = vec.knn(conn, [1.0, 0.0, 0.0], k=3)
    assert [h.chunk_id for h in hits] == [10, 30, 20]
    assert hits[0].distance <= hits[1].distance <= hits[2].distance


def test_knn_respects_k(conn):
    vec.insert_vectors(conn, [(i, [float(i), 0.0, 0.0]) for i in range(1, 6)])
    conn.commit()
    assert len(vec.knn(conn, [1.0, 0.0, 0.0], k=2)) == 2


def test_knn_zero_or_negative_returns_empty(conn):
    vec.insert_vectors(conn, [(1, [1.0, 0.0, 0.0])])
    conn.commit()
    assert vec.knn(conn, [1.0, 0.0, 0.0], k=0) == []


def test_delete_vectors(conn):
    vec.insert_vectors(conn, [(1, [1.0, 0.0, 0.0]), (2, [0.0, 1.0, 0.0])])
    conn.commit()
    vec.delete_vectors(conn, [1])
    conn.commit()
    assert vec.vec_count(conn) == 1
    assert [h.chunk_id for h in vec.knn(conn, [1.0, 0.0, 0.0], k=5)] == [2]


def test_clear_vectors(conn):
    vec.insert_vectors(conn, [(1, [1.0, 0.0, 0.0])])
    conn.commit()
    vec.clear_vectors(conn)
    conn.commit()
    assert vec.vec_count(conn) == 0


def test_wrong_dimension_is_rejected(conn):
    with pytest.raises(sqlite3.Error):
        vec.insert_vectors(conn, [(1, [1.0, 0.0])])
