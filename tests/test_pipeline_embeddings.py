"""Tests for embedding-aware indexing and backfill."""

from __future__ import annotations

import pytest

from feagle import config, db
from feagle.embeddings.client import EmbeddingError, ServerUnavailable
from feagle.indexer import pipeline
from feagle.search import vec

DIM = 4


class FakeEmbedder:
    def __init__(self, dim: int = DIM) -> None:
        self.dim = dim
        self.seen: list[tuple[str, str | None]] = []

    def embed_documents(self, items):
        self.seen.extend(items)
        return [[float(i + 1)] * self.dim for i in range(len(items))]

    def embed_queries(self, texts):
        return [[0.0] * self.dim for _ in texts]


class BrokenEmbedder(FakeEmbedder):
    def embed_documents(self, items):
        raise ServerUnavailable("boom")


class BatchOnlyEmbedder(FakeEmbedder):
    """Rejects batches with more than one input, but works input by input."""

    def embed_documents(self, items):
        if len(items) > 1:
            raise EmbeddingError("batch too large")
        return super().embed_documents(items)


class MarkerEmbedder(FakeEmbedder):
    """Always refuses chunks containing a marker (like an oversized input)."""

    def embed_documents(self, items):
        if any("MARKER" in text for text, _ in items):
            raise EmbeddingError("refusing MARKER chunk")
        return super().embed_documents(items)


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


def make_cfg(root):
    cfg = config.default_config()
    cfg["index"]["roots"] = [str(root)]
    return cfg


def chunk_count(conn):
    return conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]


def embedded_of(conn, path):
    import os

    row = conn.execute(
        "SELECT embedded FROM files WHERE path = ?", (os.path.realpath(path),)
    ).fetchone()
    return row["embedded"]


def test_index_with_embedder_stores_vectors(conn, root):
    (root / "a.txt").write_text("semantic content here", encoding="utf-8")
    embedder = FakeEmbedder()
    stats = pipeline.index_roots(conn, make_cfg(root), embedder=embedder)

    assert stats.reindexed == 1
    assert stats.vectors == chunk_count(conn) == 1
    assert vec.vec_count(conn) == 1
    assert embedded_of(conn, root / "a.txt") == 1
    assert db.get_embedding_dim(conn) == DIM
    assert embedder.seen[0][1] == "a"  # title = file stem


def test_index_without_embedder_has_no_vectors(conn, root):
    (root / "a.txt").write_text("plain text", encoding="utf-8")
    stats = pipeline.index_roots(conn, make_cfg(root))
    assert stats.vectors == 0
    assert embedded_of(conn, root / "a.txt") == 0
    assert db.has_vec_table(conn) is False


def test_embedding_failure_degrades_to_text_only(conn, root):
    (root / "a.txt").write_text("text survives", encoding="utf-8")
    stats = pipeline.index_roots(conn, make_cfg(root), embedder=BrokenEmbedder())
    assert stats.errors == 0
    assert chunk_count(conn) == 1
    assert embedded_of(conn, root / "a.txt") == 0
    assert vec.vec_count(conn) == 0


def test_reindex_replaces_vectors(conn, root):
    path = root / "a.txt"
    path.write_text("first version", encoding="utf-8")
    pipeline.index_roots(conn, make_cfg(root), embedder=FakeEmbedder())

    path.write_text("second version, changed", encoding="utf-8")
    pipeline.index_roots(conn, make_cfg(root), embedder=FakeEmbedder())

    # every stored vector points at a live chunk, and counts match
    stored = {r["chunk_id"] for r in conn.execute("SELECT chunk_id FROM chunks_vec")}
    live = {r["id"] for r in conn.execute("SELECT id FROM chunks")}
    assert stored == live
    assert len(stored) == chunk_count(conn)


def test_backfill_embeds_existing_files(conn, root):
    for i in range(3):
        (root / f"f{i}.txt").write_text(f"document number {i}", encoding="utf-8")
    pipeline.index_roots(conn, make_cfg(root))  # no embedder -> embedded=0

    done = pipeline.backfill(conn, make_cfg(root), FakeEmbedder())
    assert done == 3
    assert vec.vec_count(conn) == chunk_count(conn)
    assert all(row["embedded"] == 1 for row in conn.execute("SELECT embedded FROM files"))


def test_backfill_continues_past_a_file_that_cannot_be_embedded(conn, root):
    """One bad file must not cost the whole queue its vectors."""
    (root / "bad.txt").write_text("alpha MARKER beta", encoding="utf-8")
    (root / "good.txt").write_text("plain content", encoding="utf-8")
    pipeline.index_roots(conn, make_cfg(root))  # no embedder -> embedded=0

    done = pipeline.backfill(conn, make_cfg(root), MarkerEmbedder())
    assert done == 1
    assert embedded_of(conn, root / "good.txt") == 1
    assert embedded_of(conn, root / "bad.txt") == 0  # stays for a later pass
    assert vec.vec_count(conn) == 1


class CountingBrokenEmbedder(BrokenEmbedder):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def embed_documents(self, items):
        self.calls += 1
        return super().embed_documents(items)


def test_backfill_gives_up_when_the_server_stays_down(conn, root):
    for i in range(5):
        (root / f"f{i}.txt").write_text(f"content {i}", encoding="utf-8")
    pipeline.index_roots(conn, make_cfg(root))
    embedder = CountingBrokenEmbedder()

    assert pipeline.backfill(conn, make_cfg(root), embedder) == 0
    # a timeout per file would be pointless: it stops after a few failures
    assert embedder.calls == 3
    assert embedded_of(conn, root / "f0.txt") == 0


def test_backfill_retries_a_flaky_server(conn, root):
    """A single failure must not end the pass (the counter resets on success)."""
    for i in range(4):
        (root / f"f{i}.txt").write_text(f"content {i}", encoding="utf-8")
    pipeline.index_roots(conn, make_cfg(root))

    class FlakyOnce(FakeEmbedder):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def embed_documents(self, items):
            self.calls += 1
            if self.calls in (1, 3):
                raise ServerUnavailable("boom")
            return super().embed_documents(items)

    done = pipeline.backfill(conn, make_cfg(root), FlakyOnce())
    assert done == 2  # f1 and f2 succeeded, f0 and f3 still wait


def test_rebuild_drops_and_recreates_vectors(conn, root):
    (root / "a.txt").write_text("content", encoding="utf-8")
    pipeline.index_roots(conn, make_cfg(root), embedder=FakeEmbedder())
    assert db.has_vec_table(conn) is True

    pipeline.index_roots(conn, make_cfg(root), rebuild=True, embedder=FakeEmbedder())
    assert db.get_embedding_dim(conn) == DIM
    assert vec.vec_count(conn) == chunk_count(conn) == 1


def test_prune_removes_vectors(conn, root):
    path = root / "a.txt"
    path.write_text("content", encoding="utf-8")
    pipeline.index_roots(conn, make_cfg(root), embedder=FakeEmbedder())
    path.unlink()
    pipeline.index_roots(conn, make_cfg(root), embedder=FakeEmbedder())
    assert vec.vec_count(conn) == 0


def test_batch_failure_falls_back_to_per_chunk(conn, root):
    (root / "long.txt").write_text(" ".join(f"word{i}" for i in range(600)), encoding="utf-8")
    stats = pipeline.index_roots(conn, make_cfg(root), embedder=BatchOnlyEmbedder())

    assert chunk_count(conn) > 1  # the batch was actually multi-chunk
    assert stats.vectors == chunk_count(conn)
    assert vec.vec_count(conn) == chunk_count(conn)
    assert embedded_of(conn, root / "long.txt") == 1


def test_one_unembeddable_chunk_does_not_lose_the_file(conn, root):
    body = ("alpha beta gamma delta " * 120) + "\nMARKER\n" + ("epsilon zeta eta " * 120)
    (root / "mixed.txt").write_text(body, encoding="utf-8")
    stats = pipeline.index_roots(conn, make_cfg(root), embedder=MarkerEmbedder())

    total = chunk_count(conn)
    assert total > 1
    assert 0 < stats.vectors < total  # the MARKER chunk was skipped
    assert embedded_of(conn, root / "mixed.txt") == 0  # so backfill can retry
