"""Tests for full-text search."""

from __future__ import annotations

import time

import pytest

from feagle import db
from feagle.search.fts import fts_terms, search_fts


@pytest.fixture
def conn(tmp_path):
    connection = db.open_database(tmp_path / "search.sqlite")
    yield connection
    connection.close()


def add_doc(conn, path, body, *, page=None, line=None, seq=0):
    cur = conn.execute(
        "INSERT INTO files(path, size, mtime_ns, state, updated_at) VALUES(?,?,?,'ok',?)",
        (path, len(body), 1, int(time.time())),
    )
    file_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO chunks(file_id, seq, page, line, body) VALUES(?,?,?,?,?)",
        (file_id, seq, page, line, body),
    )
    chunk_id = cur.lastrowid
    conn.execute("INSERT INTO chunks_fts(body, chunk_id) VALUES(?, ?)", (body, chunk_id))
    conn.commit()


def test_single_term_match(conn):
    add_doc(conn, "/tmp/a.txt", "the quick brown fox", line=12)
    hits = search_fts(conn, "brown")
    assert len(hits) == 1
    assert hits[0].path == "/tmp/a.txt"
    assert hits[0].line == 12
    assert "brown" in hits[0].snippet


def test_and_then_or_fallback(conn):
    add_doc(conn, "/tmp/a.txt", "alpha beta", seq=0)
    add_doc(conn, "/tmp/b.txt", "alpha gamma", seq=0)

    hits = search_fts(conn, "alpha delta")  # no doc has both -> OR fallback
    assert {h.path for h in hits} == {"/tmp/a.txt", "/tmp/b.txt"}

    hits = search_fts(conn, "alpha beta")  # AND matches a.txt only
    assert {h.path for h in hits} == {"/tmp/a.txt"}


def test_fts_operators_are_neutralized(conn):
    add_doc(conn, "/tmp/a.txt", "the OR NEAR clause", seq=0)
    # a query full of FTS5 syntax must not raise and must be treated literally
    for query in ['OR', '"', '*', '-', 'NEAR', 'title:"OR"', 'a - b * c']:
        search_fts(conn, query)  # should not raise


def test_limit_is_respected(conn):
    for i in range(10):
        add_doc(conn, f"/tmp/{i}.txt", "common token", seq=0)
    assert len(search_fts(conn, "common", limit=3)) == 3


def test_empty_query_returns_nothing(conn):
    add_doc(conn, "/tmp/a.txt", "content", seq=0)
    assert search_fts(conn, "") == []
    assert search_fts(conn, "   ") == []
    assert search_fts(conn, "!!!") == []


def test_page_and_line_are_reported(conn):
    add_doc(conn, "/tmp/doc.pdf", "page body text", page=7, seq=0)
    hits = search_fts(conn, "page")
    assert hits[0].page == 7
    assert hits[0].line is None


def test_snippet_is_flattened(conn):
    add_doc(conn, "/tmp/a.txt", "line one\nline two\n\nline three", seq=0)
    hits = search_fts(conn, "three")
    assert "\n" not in hits[0].snippet


def test_fts_terms_strips_quotes():
    assert fts_terms('hello "world"') == ["hello", "world"]
    assert fts_terms('   ') == []
