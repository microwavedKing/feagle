"""``feagle bench``: synthetic indexing/query benchmarks and quality metrics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .. import bench


def print_report(result: dict[str, Any], corpus: Path) -> None:
    indexing = result["indexing"]
    print(f"Corpus:   {result['files']} documents (seed {result['seed']}) in {corpus}")
    print(
        f"Indexing: {indexing['chunks']} chunks in {indexing['seconds']} s "
        f"({indexing['chunks_per_second']} chunks/s, {indexing['files_per_second']} files/s)"
    )
    print(
        f"          vectors {indexing['vectors']} (coverage {indexing['coverage']:.0%}), "
        f"cold start {indexing['cold_start_s']} s"
    )
    print(f"Database: {indexing['db_size_bytes'] / (1024 * 1024):.1f} MiB "
          f"({indexing['db_size_per_chunk']} bytes/chunk)")
    rss = indexing.get("peak_rss_self_mb")
    llama = result.get("llama_server_rss_mb")
    print(f"Memory:   feagle +{rss if rss is not None else '?'} MiB, "
          f"llama-server {llama if llama is not None else '?'} MiB")
    for name, stats in result["queries"].items():
        if stats["count"]:
            print(f"Query {name:>13}: p50 {stats['p50']} ms, p95 {stats['p95']} ms "
                  f"(min {stats['min']}, max {stats['max']}, n={stats['count']})")
    for mode, metrics in result.get("quality", {}).items():
        parts = [f"MRR@10 {metrics['mrr@10']:.3f}", f"Recall@20 {metrics['recall@20']:.3f}"]
        for lang in ("en", "ru"):
            key = f"mrr@10_{lang}"
            if key in metrics:
                parts.append(f"MRR@10({lang}) {metrics[key]:.3f}")
        print(f"Quality {mode:>6}: " + ", ".join(parts))


def run(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    corpus = Path(args.corpus) if args.corpus else bench.default_corpus_dir()
    result = bench.run_bench(
        cfg,
        corpus_dir=corpus,
        files=args.files,
        seed=args.seed,
        query_limit=args.queries,
        repeats=args.repeats,
        limit=args.limit,
        embed=not args.no_embed,
        keep_db=args.keep_db,
    )

    if args.json or args.out:
        payload = json.dumps(result, ensure_ascii=False, indent=2)
        if args.json:
            print(payload)
        if args.out:
            target = Path(args.out)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(payload + "\n", encoding="utf-8")
            print(f"wrote {target}")

    print_report(result, corpus)
    return 0
