"""Evaluate retrieval quality on a corpus you already have.

``quality_eval.py`` generates a synthetic corpus with its own queries; this one
points at a directory that is already on disk plus a JSON query file, so a
"real text" corpus can be measured with the same MRR@10 / Recall@10/20 metrics:

    python bench/corpus_eval.py --corpus ~/books --queries queries.json --mrl-dim 256

The query file is a JSON list of objects:

    [{"concept": "alice", "expected": "alice.txt", "query": "…", "lang": "en"}]

``expected`` is the file name that should be found; queries that expect the same
file share a ``concept`` (any of its files counts as a hit). ``lang`` is optional
and only splits the per-language report.

The corpus is indexed into a throwaway database; nothing outside the corpus
directory and the query file is read.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from feagle import bench, config, db
from feagle.embeddings import presets
from feagle.embeddings.client import EmbeddingError, ServerUnavailable
from feagle.embeddings.service import EmbeddingService, ModelMissingError
from feagle.indexer import pipeline

#: Failures that mean "index without embeddings" rather than "give up".
_DEGRADE_ERRORS = (ModelMissingError, ServerUnavailable, EmbeddingError, OSError)


def load_queries(path: Path) -> list[dict]:
    items = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(items, list) or not items:
        raise SystemExit(f"{path}: expected a non-empty JSON list")
    for item in items:
        if not {"query", "expected"} <= set(item):
            raise SystemExit(f"{path}: every query needs 'query' and 'expected'")
        item.setdefault("concept", item["expected"])
    return items


def _sweep_guard(conn: Any, cfg: dict[str, Any], db_path: Path, dim: int | None) -> None:
    """Refuse to reuse a database built with another model or dimension.

    A threshold sweep reuses one database on purpose; a *different* model would
    silently compare vectors from two embedding spaces.
    """
    stored_model = db.get_meta(conn, "model_id")
    model = str(cfg["embeddings"]["model"])
    if stored_model and stored_model != model:
        raise SystemExit(
            f"{db_path} was built with model {stored_model!r}, this run uses {model!r}; "
            "use another --db or delete this one"
        )
    stored_dim = db.get_embedding_dim(conn)
    if dim and stored_dim and stored_dim != dim:
        raise SystemExit(
            f"{db_path} holds {stored_dim}-dimensional vectors, this run would use {dim}; "
            "use another --db or delete this one"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure retrieval quality on a local corpus.")
    parser.add_argument("--corpus", type=Path, required=True, help="directory to index")
    parser.add_argument("--queries", type=Path, required=True, help="JSON query file")
    parser.add_argument("--limit", type=int, default=20, help="results per query")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    parser.add_argument(
        "--model", default=None, help="override [embeddings].model (a preset name or a GGUF path)"
    )
    parser.add_argument(
        "--mrl-dim", type=int, default=None, choices=presets.MRL_DIMS,
        help="override [embeddings].mrl_dim (Matryoshka truncation)",
    )
    parser.add_argument(
        "--max-distance", type=float, default=None, metavar="D",
        help="override [search].max_distance (L2; 0 disables the threshold)",
    )
    parser.add_argument(
        "--db", type=Path, default=None, metavar="FILE",
        help="reuse this database instead of a throwaway one (sweeps skip indexing)",
    )
    args = parser.parse_args(argv)

    corpus = args.corpus.expanduser().resolve()
    if not corpus.is_dir():
        raise SystemExit(f"not a directory: {corpus}")
    queries = load_queries(args.queries)

    cfg = config.load_config()
    if args.model:
        cfg["embeddings"]["model"] = args.model
    if args.mrl_dim:
        cfg["embeddings"]["mrl_dim"] = args.mrl_dim
    if args.max_distance is not None:
        # Any value that differs from the built-in default is treated as explicit
        # by `hybrid.effective_max_distance`, which is what we want here.
        cfg["search"]["max_distance"] = args.max_distance

    if args.db is not None:
        db_path = args.db.expanduser().resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        db_path = Path(tempfile.mkdtemp(prefix="feagle-corpus-eval-")) / "corpus.sqlite"
    reuse = db_path.exists()
    workdir = db_path.parent
    service = EmbeddingService(cfg, runtime_dir=workdir)
    embedder = None
    try:
        try:
            embedder = service.start()
        except _DEGRADE_ERRORS as exc:
            print(f"running without embeddings: {exc}", file=sys.stderr)
        conn = db.open_database(db_path)
        try:
            if reuse:
                _sweep_guard(conn, cfg, db_path, embedder.dim if embedder else None)
                stats = pipeline.IndexStats()
                index_s = 0.0
            else:
                started = time.perf_counter()
                stats = pipeline.index_roots(conn, cfg, roots=[str(corpus)], embedder=embedder)
                index_s = time.perf_counter() - started
            chunks = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
            files = len(conn.execute("SELECT id FROM files").fetchall())
            db_size = db_path.stat().st_size
            quality = bench.evaluate_quality(conn, embedder, queries, cfg=cfg, limit=args.limit)
        finally:
            conn.close()
    finally:
        service.close()
        if args.db is None:
            shutil.rmtree(workdir, ignore_errors=True)

    report = {
        "corpus": str(corpus),
        "files": files if reuse else stats.reindexed,
        "reused_db": reuse,
        "chunks": chunks,
        "queries": len(queries),
        "model": cfg["embeddings"]["model"],
        "mrl_dim": cfg["embeddings"]["mrl_dim"],
        "max_distance": args.max_distance,
        "index_seconds": round(index_s, 2),
        "db_size_bytes": db_size,
        "db_size_per_chunk": round(db_size / chunks) if chunks else 0,
        "quality": quality,
    }

    if args.json:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0

    print(
        f"Corpus: {report['files']} files, {report['chunks']} chunks, "
        f"{report['queries']} queries"
    )
    print(
        f"Model: {report['model']} at mrl_dim={report['mrl_dim']} · "
        f"indexed in {report['index_seconds']}s · DB {report['db_size_per_chunk']} B/chunk"
    )
    if report["max_distance"] is not None:
        print(f"max_distance override: {report['max_distance']}")
    for mode, metrics in report["quality"].items():
        parts = [
            f"MRR@10={metrics['mrr@10']:.3f}",
            f"Recall@10={metrics['recall@10']:.3f}",
            f"Recall@20={metrics['recall@20']:.3f}",
        ]
        print(f"  {mode:6}  " + "  ".join(parts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
