"""One-shot indexing command (with optional embeddings and backfill)."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from .. import db, paths
from ..embeddings.client import EmbeddingError, ServerUnavailable
from ..embeddings.service import EmbeddingService, ModelMissingError
from ..indexer import pipeline

_DEGRADE_ERRORS = (ModelMissingError, ServerUnavailable, EmbeddingError, OSError)


def _start_embedder(cfg: dict[str, Any]) -> tuple[EmbeddingService | None, Any]:
    service = EmbeddingService(cfg)
    try:
        return service, service.start()
    except _DEGRADE_ERRORS as exc:
        print(
            f"feagle: semantic indexing disabled ({exc}); "
            "indexing text only (run 'feagle setup' to enable embeddings)",
            file=sys.stderr,
        )
        service.close(stop_owned=False)
        return None, None


def run(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    roots = args.root
    if not args.backfill and roots is None and not cfg["index"]["roots"]:
        print(
            "feagle: no roots configured.\n"
            f"Add directories to [index].roots in {paths.config_file()}, "
            "or pass --root PATH.",
            file=sys.stderr,
        )
        return 1

    service = None
    embedder = None
    if not args.no_embed:
        service, embedder = _start_embedder(cfg)

    conn = db.open_database()
    try:
        if args.backfill:
            if embedder is None:
                print("feagle: backfill needs embeddings; nothing to do.", file=sys.stderr)
                return 1
            count = pipeline.backfill(conn, cfg, embedder)
            print(f"backfilled {count} file(s)")
            return 0
        stats = pipeline.index_roots(
            conn, cfg, roots=roots, full=args.full, rebuild=args.rebuild, embedder=embedder
        )
    except db.DimMismatchError as exc:
        print(f"feagle: {exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()
        if service is not None:
            service.close()

    print(stats.summary())
    for sample in stats.error_samples:
        print(f"feagle: failed to index {sample}", file=sys.stderr)
    return 1 if stats.errors else 0
