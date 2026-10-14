"""Search command (full-text only until the semantic phase lands)."""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from .. import db, paths
from ..search.fts import search_fts


def _location(hit: Any) -> str:
    location = hit.path
    if hit.line is not None:
        location += f":{hit.line}"
    if hit.page is not None:
        location += f" (page {hit.page})"
    return location


def run(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    db_path = paths.db_path()
    if not db_path.exists():
        print("feagle: no index yet; run 'feagle index' first.", file=sys.stderr)
        return 1

    limit = args.limit if args.limit is not None else cfg["search"]["limit"]
    conn = db.connect(read_only=True)
    try:
        started = time.perf_counter()
        hits = search_fts(conn, args.query, limit=limit)
        took_ms = round((time.perf_counter() - started) * 1000, 1)
    finally:
        conn.close()

    if args.json:
        payload = {
            "query": args.query,
            "mode": "fts",
            "took_ms": took_ms,
            "results": [
                {
                    "rank": rank,
                    "score": hit.score,
                    "path": hit.path,
                    "page": hit.page,
                    "line": hit.line,
                    "snippet": hit.snippet,
                }
                for rank, hit in enumerate(hits, start=1)
            ],
        }
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    elif hits:
        for rank, hit in enumerate(hits, start=1):
            print(f"{rank}. {_location(hit)}")
            print(f"   {hit.snippet}")
    else:
        print("No results.")

    return 0
