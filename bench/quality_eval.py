"""Quality evaluation (MRR@10, Recall@10/20) on a synthetic corpus.

Indexes the generated corpus into a throwaway database and scores how often the
expected document is found; works for full-text and hybrid search and reports
English and Russian separately.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from feagle import bench, config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure retrieval quality on synthetic Q&A.")
    parser.add_argument("--corpus", type=Path, default=None, help="corpus directory")
    parser.add_argument("--files", type=int, default=60, help="documents to generate")
    parser.add_argument("--seed", type=int, default=1, help="random seed")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    parser.add_argument("--keep-db", action="store_true", help="keep the throwaway database")
    args = parser.parse_args(argv)

    report = bench.quality_report(
        config.load_config(),
        corpus_dir=args.corpus,
        files=args.files,
        seed=args.seed,
        keep_db=args.keep_db,
    )

    if args.json:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0

    print(
        f"Quality: {report['files']} documents, {report['queries']} queries "
        f"(seed {report['seed']})"
    )
    for mode, metrics in report["quality"].items():
        parts = [f"MRR@10={metrics['mrr@10']:.3f}", f"Recall@10={metrics['recall@10']:.3f}",
                 f"Recall@20={metrics['recall@20']:.3f}"]
        for lang in ("en", "ru"):
            key = f"mrr@10_{lang}"
            if key in metrics:
                parts.append(f"MRR@10({lang})={metrics[key]:.3f}")
        print(f"  {mode:>6}: " + "  ".join(parts))
        if metrics["misses"]:
            print(f"          misses: {', '.join(metrics['misses'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
