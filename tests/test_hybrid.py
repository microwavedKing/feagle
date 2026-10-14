"""Tests for hybrid (RRF) search."""

from __future__ import annotations

import time

import pytest

from feagle import db
from feagle.search import hybrid, vec

DIM = 3


@pytest.fixture
def conn(tmp_path):
    connection = db.open_database(tmp_path / "hybrid.sqlite")
    db.ensure_vec_table(connection, DIM)
    yield connection
    connection.close()


def add(conn, path, body, vector, *, page=None, line=None):
    now = int(time.time())
    cur = conn.execute(
        "INSERT INTO files(path, size, mtime_ns, state, updated_at) VALUES(?,?,?,'ok',?)",
        (path, len(body), 1, now),
    )
    file_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO chunks(file_id, seq, page, line, body) VALUES(?,?,?,?,?)",
        (file_id, 0, page, line, body),
    )
    chunk_id = cur.lastrowid
    conn.execute("INSERT INTO chunks_fts(body, chunk_id) VALUES(?, ?)", (body, chunk_id))
    vec.insert_vectors(conn, [(chunk_id, vector)])
    conn.commit()
    return chunk_id


def test_rrf_scores_and_ordering():
    fused = hybrid.rrf([[1, 2, 3], [3, 1]])
    scores = dict(fused)
    assert scores[1] == pytest.approx(1 / 61 + 1 / 62)
    assert scores[3] == pytest.approx(1 / 63 + 1 / 61)
    # 1 appears high in both lists -> leads
    assert fused[0][0] == 1


def test_rrf_tie_breaks_by_chunk_id():
    fused = hybrid.rrf([[], []])
    assert fused == []
    assert hybrid.rrf([[5], [5]]) == [(5, pytest.approx(2 / 61))]


def test_hybrid_finds_document_by_meaning_only(conn):
    # "alpha" matches the query by words; "beta" only by vector proximity
    add(conn, "/d/alpha.txt", "alpha words here", [0.0, 1.0, 0.0], line=1)
    beta = add(conn, "/d/beta.txt", "unrelated text", [1.0, 0.0, 0.0], line=5)

    hits = hybrid.search_hybrid(
        conn, "alpha", query_vector=[1.0, 0.0, 0.0], limit=10, candidates=10
    )
    paths = [h.path for h in hits]
    assert "/d/alpha.txt" in paths
    assert "/d/beta.txt" in paths  # surfaced by the vector side
    beta_hit = next(h for h in hits if h.chunk_id == beta)
    assert beta_hit.line == 5


def test_hybrid_without_vector_is_fts_only(conn):
    add(conn, "/d/alpha.txt", "alpha words here", [0.0, 1.0, 0.0])
    add(conn, "/d/beta.txt", "unrelated text", [1.0, 0.0, 0.0])

    hits = hybrid.search_hybrid(conn, "alpha", query_vector=None, limit=10, candidates=10)
    assert [h.path for h in hits] == ["/d/alpha.txt"]


def test_hybrid_limit_is_respected(conn):
    for i in range(5):
        add(conn, f"/d/{i}.txt", f"alpha token {i}", [float(i + 1), 0.0, 0.0])
    hits = hybrid.search_hybrid(
        conn, "alpha", query_vector=[1.0, 0.0, 0.0], limit=2, candidates=10
    )
    assert len(hits) == 2


def test_hybrid_no_matches_returns_empty(conn):
    add(conn, "/d/a.txt", "alpha", [1.0, 0.0, 0.0])
    assert hybrid.search_hybrid(conn, "zzz", query_vector=None, limit=5, candidates=5) == []


def test_snippet_is_flattened(conn):
    add(conn, "/d/a.txt", "first line\nsecond line", [1.0, 0.0, 0.0])
    hits = hybrid.search_hybrid(conn, "first", query_vector=None, limit=5, candidates=5)
    assert "\n" not in hits[0].snippet


def test_hybrid_ignores_single_word_or_matches(conn):
    # "noise" shares one query word with the query; "target" is the semantic match
    add(conn, "/d/noise.txt", "need something unrelated", [0.0, 0.0, 1.0])
    target = add(conn, "/d/target.txt", "totally different words", [1.0, 0.0, 0.0])

    hits = hybrid.search_hybrid(
        conn, "sleep hours need", query_vector=[1.0, 0.0, 0.0], limit=5, candidates=10
    )
    assert hits[0].chunk_id == target
