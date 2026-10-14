"""One-shot indexing command."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from .. import db, paths
from ..indexer import pipeline


def run(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    roots = args.root
    if roots is None and not cfg["index"]["roots"]:
        print(
            "feagle: no roots configured.\n"
            f"Add directories to [index].roots in {paths.config_file()}, "
            "or pass --root PATH.",
            file=sys.stderr,
        )
        return 1

    conn = db.open_database()
    try:
        stats = pipeline.index_roots(
            conn, cfg, roots=roots, full=args.full, rebuild=args.rebuild
        )
    finally:
        conn.close()

    print(stats.summary())
    for sample in stats.error_samples:
        print(f"feagle: failed to index {sample}", file=sys.stderr)
    return 1 if stats.errors else 0
