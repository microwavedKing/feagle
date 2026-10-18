"""Tests for the synthetic corpus, metrics and quality evaluation."""

from __future__ import annotations

import hashlib
import json
import time

import pytest

from feagle import bench, config, db, metrics
from feagle.search import vec

# -- corpus -----------------------------------------------------------------


def test_generate_corpus_is_deterministic(tmp_path):
    first = tmp_path / "a"
    second = tmp_path / "b"
    bench.generate_corpus(first, files=6, seed=7)
    bench.generate_corpus(second, files=6, seed=7)

    for name in sorted(p.name for p in (first / "docs").iterdir()):
        digest_a = hashlib.sha256((first / "docs" / name).read_bytes()).hexdigest()
        digest_b = hashlib.sha256((second / "docs" / name).read_bytes()).hexdigest()
        assert digest_a == digest_b


def test_generate_corpus_layout(tmp_path):
    manifest = bench.generate_corpus(tmp_path, files=5, seed=1)
    assert len(manifest["files"]) == 5
    assert len(manifest["queries"]) == 5
    assert (tmp_path / "docs").is_dir()
    assert (tmp_path / "queries.json").is_file()

    stored = json.loads((tmp_path / "queries.json").read_text(encoding="utf-8"))
    assert stored == manifest
    # documents use several extensions and both languages
    suffixes = {name.rsplit(".", 1)[1] for name in (p.name for p in (tmp_path / "docs").iterdir())}
    assert len(suffixes) >= 2
    assert {item["lang"] for item in manifest["files"]} == {"en", "ru"}


def test_generate_corpus_clears_a_previous_stale_corpus(tmp_path):
    """The default corpus dir is shared: leftovers must not become noise."""
    bench.generate_corpus(tmp_path, files=8, seed=1)
    assert len(list((tmp_path / "docs").iterdir())) == 8

    manifest = bench.generate_corpus(tmp_path, files=3, seed=1)
    names = sorted(p.name for p in (tmp_path / "docs").iterdir())
    assert names == sorted(item["name"] for item in manifest["files"])


def test_generated_documents_are_not_empty(tmp_path):
    bench.generate_corpus(tmp_path, files=3, seed=2)
    for path in (tmp_path / "docs").iterdir():
        assert path.stat().st_size > 200


# -- metrics ----------------------------------------------------------------


def test_percentile_and_summarise():
    values = [float(i) for i in range(1, 11)]
    assert metrics.percentile(values, 50) == 5.0
    assert metrics.percentile(values, 95) == 10.0
    summary = metrics.summarise(values)
    assert summary["count"] == 10
    assert summary["min"] == 1.0 and summary["max"] == 10.0
    assert metrics.summarise([])["count"] == 0


def test_peak_rss_is_readable():
    # the own-process figure is available on Linux
    assert metrics.peak_rss_mb() is None or metrics.peak_rss_mb() > 0


# -- quality evaluation -----------------------------------------------------


@pytest.fixture
def conn(tmp_path):
    connection = db.open_database(tmp_path / "bench.sqlite")
    db.ensure_vec_table(connection, 3)
    now = int(time.time())
    for name, body, vector in [
        ("sleep-doc.txt", "sleep night rest recovery hours adults", [1.0, 0.0, 0.0]),
        ("bread-doc.txt", "bread flour dough yeast oven bake", [0.0, 1.0, 0.0]),
    ]:
        cur = connection.execute(
            "INSERT INTO files(path, size, mtime_ns, state, updated_at) VALUES(?,?,?,'ok',?)",
            (f"/d/{name}", len(body), 1, now),
        )
        file_id = cur.lastrowid
        cur = connection.execute(
            "INSERT INTO chunks(file_id, seq, line, body) VALUES(?,?,?,?)",
            (file_id, 0, 1, body),
        )
        chunk_id = cur.lastrowid
        connection.execute(
            "INSERT INTO chunks_fts(body, stem, chunk_id) VALUES(?,?,?)",
            (body, "", chunk_id),
        )
        vec.insert_vectors(connection, [(chunk_id, vector)])
    connection.commit()
    yield connection
    connection.close()


def test_evaluate_quality_perfect_and_miss(conn):
    cfg = config.default_config()
    queries = [
        {"query": "sleep", "expected": "sleep-doc.txt", "lang": "en", "concept": 0},
        {"query": "zzzz-nothing", "expected": "bread-doc.txt", "lang": "en", "concept": 1},
    ]
    quality = bench.evaluate_quality(conn, None, queries, cfg=cfg)
    assert quality["fts"]["mrr@10"] == pytest.approx(0.5)  # one hit at rank 1, one miss
    assert quality["fts"]["recall@10"] == pytest.approx(0.5)
    assert quality["fts"]["mrr@10_en"] == pytest.approx(0.5)


def test_evaluate_quality_accepts_sibling_documents(conn):
    """Any document of the expected concept counts as a hit."""
    cfg = config.default_config()
    queries = [
        {"query": "sleep", "expected": "other-sleep-doc.txt", "lang": "en", "concept": 0},
        {"query": "sleep", "expected": "sleep-doc.txt", "lang": "ru", "concept": 0},
    ]
    quality = bench.evaluate_quality(conn, None, queries, cfg=cfg)
    assert quality["fts"]["mrr@10"] == pytest.approx(1.0)
