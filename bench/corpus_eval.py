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

    workdir = Path(tempfile.mkdtemp(prefix="feagle-corpus-eval-"))
    db_path = workdir / "corpus.sqlite"
    service = EmbeddingService(cfg, runtime_dir=workdir)
    embedder = None
    try:
        try:
            embedder = service.start()
        except _DEGRADE_ERRORS as exc:
            print(f"running without embeddings: {exc}", file=sys.stderr)
        conn = db.open_database(db_path)
        try:
            started = time.perf_counter()
            stats = pipeline.index_roots(conn, cfg, roots=[str(corpus)], embedder=embedder)
            index_s = time.perf_counter() - started
            chunks = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
            db_size = db_path.stat().st_size
            quality = bench.evaluate_quality(conn, embedder, queries, cfg=cfg, limit=args.limit)
        finally:
            conn.close()
    finally:
        service.close()
        shutil.rmtree(workdir, ignore_errors=True)

    report = {
        "corpus": str(corpus),
        "files": stats.reindexed,
        "chunks": chunks,
        "queries": len(queries),
        "model": cfg["embeddings"]["model"],
        "mrl_dim": cfg["embeddings"]["mrl_dim"],
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
