"""End-to-end test against a real llama-server and the real model.

Skipped unless ``FEAGLE_RUN_MODEL_TESTS=1`` and both ``llama-server`` and the
model file are present. It never reads personal files: the corpus is created in
``tmp_path`` and the database lives there too. Only the model and the runtime
directory are shared with the user's environment.
"""

from __future__ import annotations

import os
import shutil

import pytest

from feagle import config, db, paths
from feagle.embeddings.presets import get_preset
from feagle.embeddings.service import EmbeddingService
from feagle.indexer import pipeline

_RUN = os.environ.get("FEAGLE_RUN_MODEL_TESTS") == "1" and shutil.which("llama-server") is not None
_MODEL = paths.models_dir() / get_preset("embeddinggemma-2-q8").filename

pytestmark = pytest.mark.skipif(
    not _RUN or not _MODEL.exists(),
    reason="set FEAGLE_RUN_MODEL_TESTS=1; requires llama-server and the downloaded model",
)


def test_semantic_search_ranks_by_meaning(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "recipe.txt").write_text(
        "Boil the pasta until al dente, then stir in the tomato sauce and basil.",
        encoding="utf-8",
    )
    (docs / "astronomy.txt").write_text(
        "A black hole is a region where gravity is so strong that nothing can "
        "escape, not even light.",
        encoding="utf-8",
    )
    (docs / "code.txt").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    cfg = config.default_config()
    cfg["index"]["roots"] = [str(docs)]

    service = EmbeddingService(cfg)
    try:
        embedder = service.start()
        assert embedder.dim == cfg["embeddings"]["mrl_dim"]

        conn = db.open_database(tmp_path / "index.sqlite")
        try:
            stats = pipeline.index_roots(conn, cfg, embedder=embedder)
            assert stats.reindexed == 3
            assert stats.vectors == 3

            vector = embedder.embed_queries(["how are black holes formed"])[0]
            from feagle.search import hybrid

            hits = hybrid.search_hybrid(
                conn, "black holes", query_vector=vector, limit=3, candidates=10
            )
        finally:
            conn.close()
    finally:
        service.close()

    assert hits
    assert hits[0].path.endswith("astronomy.txt")
