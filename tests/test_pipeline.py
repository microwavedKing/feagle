"""Tests for the incremental indexing pipeline."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from feagle import config, db
from feagle.indexer import extract, pipeline
from feagle.indexer.extract import ExtractionError
from feagle.search.fts import search_fts

PDF_FIXTURE = Path(__file__).parent / "fixtures" / "two_pages.pdf"


@pytest.fixture
def conn(tmp_path):
    connection = db.open_database(tmp_path / "index.sqlite")
    yield connection
    connection.close()


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "root"
    path.mkdir()
    return path


def make_cfg(root, **index_overrides):
    cfg = config.default_config()
    cfg["index"]["roots"] = [str(root)]
    cfg["index"].update(index_overrides)
    return cfg


def fts_hits(conn, term):
    rows = conn.execute(
        "SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH ?", (f'"{term}"',)
    ).fetchall()
    return [r["chunk_id"] for r in rows]


def file_row(conn, path):
    return conn.execute("SELECT * FROM files WHERE path = ?", (os.path.realpath(path),)).fetchone()


def test_initial_index(root, conn):
    (root / "a.txt").write_text("hello world", encoding="utf-8")
    (root / "b.md").write_text("second document", encoding="utf-8")

    stats = pipeline.index_roots(conn, make_cfg(root))
    assert stats.reindexed == 2
    assert stats.errors == 0
    assert stats.chunks == 2
    assert conn.execute("SELECT COUNT(*) AS n FROM files").fetchone()["n"] == 2


def test_second_run_is_unchanged(root, conn):
    (root / "a.txt").write_text("hello world", encoding="utf-8")
    pipeline.index_roots(conn, make_cfg(root))
    stats = pipeline.index_roots(conn, make_cfg(root))
    assert stats.unchanged == 1
    assert stats.reindexed == 0
    assert stats.chunks == 0


def test_mtime_only_change_touches(root, conn):
    path = root / "a.txt"
    path.write_text("stable content", encoding="utf-8")
    pipeline.index_roots(conn, make_cfg(root))
    before = file_row(conn, path)
    before_chunks = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]

    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000_000))

    stats = pipeline.index_roots(conn, make_cfg(root))
    assert stats.touched == 1
    assert stats.reindexed == 0
    after = file_row(conn, path)
    assert after["id"] == before["id"]
    assert after["mtime_ns"] != before["mtime_ns"]
    assert conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"] == before_chunks


def test_content_change_replaces_old_chunks_and_fts(root, conn):
    path = root / "a.txt"
    path.write_text("alpha content", encoding="utf-8")
    pipeline.index_roots(conn, make_cfg(root))
    assert fts_hits(conn, "alpha")

    path.write_text("beta content", encoding="utf-8")
    stats = pipeline.index_roots(conn, make_cfg(root))
    assert stats.reindexed == 1
    assert fts_hits(conn, "alpha") == []
    assert fts_hits(conn, "beta")


def test_binary_file_is_unsupported(root, conn):
    (root / "blob.txt").write_bytes(b"MZ\x00\x00binary")
    stats = pipeline.index_roots(conn, make_cfg(root))
    assert stats.unsupported == 1
    assert file_row(conn, root / "blob.txt")["state"] == "unsupported"


def test_pdf_pages_are_indexed(root, conn):
    if not extract.pdftotext_available():
        pytest.skip("pdftotext not installed")
    shutil.copy(PDF_FIXTURE, root / "doc.pdf")

    stats = pipeline.index_roots(conn, make_cfg(root))
    assert stats.reindexed == 1
    pages = [row["page"] for row in conn.execute("SELECT page FROM chunks ORDER BY seq")]
    assert pages == [1, 2]  # one chunk per page, page numbers preserved

    hits = search_fts(conn, "heading")
    assert {hit.page for hit in hits} == {1, 2}


def test_invalid_pdf_is_recorded_as_error(root, conn):
    if not extract.pdftotext_available():
        pytest.skip("pdftotext not installed")
    (root / "bad.pdf").write_bytes(b"%PDF-1.7 not really a pdf")
    stats = pipeline.index_roots(conn, make_cfg(root))
    assert stats.errors == 1
    assert file_row(conn, root / "bad.pdf")["state"] == "error"


def test_extraction_error_is_recorded(root, conn, monkeypatch):
    path = root / "a.txt"
    path.write_text("content", encoding="utf-8")

    def boom(*args, **kwargs):
        raise ExtractionError("boom")

    monkeypatch.setattr(pipeline, "extract", boom)
    stats = pipeline.index_roots(conn, make_cfg(root))
    assert stats.errors == 1
    assert file_row(conn, path)["state"] == "error"


def test_max_chunks_per_file(root, conn):
    body = "\n".join(f"line {i} " + "x" * 60 for i in range(300))
    (root / "big.txt").write_text(body, encoding="utf-8")
    stats = pipeline.index_roots(conn, make_cfg(root, max_chunks_per_file=3))
    assert stats.chunks == 3
    assert conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"] == 3


def test_full_forces_reindex(root, conn):
    (root / "a.txt").write_text("content", encoding="utf-8")
    pipeline.index_roots(conn, make_cfg(root))
    stats = pipeline.index_roots(conn, make_cfg(root), full=True)
    assert stats.reindexed == 1
    assert stats.unchanged == 0


def test_rebuild_wipes_and_rescans(root, conn):
    (root / "a.txt").write_text("content", encoding="utf-8")
    (root / "b.txt").write_text("more content", encoding="utf-8")
    pipeline.index_roots(conn, make_cfg(root))
    stats = pipeline.index_roots(conn, make_cfg(root), rebuild=True)
    assert stats.reindexed == 2
    assert conn.execute("SELECT COUNT(*) AS n FROM files").fetchone()["n"] == 2


def test_removed_file_is_pruned(root, conn):
    path = root / "a.txt"
    path.write_text("content", encoding="utf-8")
    (root / "b.txt").write_text("keep me", encoding="utf-8")
    pipeline.index_roots(conn, make_cfg(root))

    path.unlink()
    stats = pipeline.index_roots(conn, make_cfg(root))
    assert stats.pruned == 1
    assert file_row(conn, path) is None
    assert conn.execute("SELECT COUNT(*) AS n FROM files").fetchone()["n"] == 1


def test_pruning_only_affects_scanned_roots(root, conn):
    other = root.parent / "other"
    other.mkdir()
    (other / "keep.txt").write_text("outside", encoding="utf-8")

    cfg = make_cfg(root)
    cfg["index"]["roots"] = [str(root), str(other)]
    (root / "a.txt").write_text("inside", encoding="utf-8")
    pipeline.index_roots(conn, cfg)

    # next run scans only ``root``: the file under ``other`` must survive
    stats = pipeline.index_roots(conn, make_cfg(root))
    assert stats.pruned == 0
    assert file_row(conn, other / "keep.txt") is not None


def test_missing_root_is_skipped(root, conn):
    stats = pipeline.index_roots(conn, make_cfg(root / "nope"))
    assert stats.scanned == 0
