"""Search command: hybrid (semantic + full-text) with graceful degradation."""

from __future__ import annotations

import json
import sys
import time
from typing import TYPE_CHECKING, Any

from .. import db, paths
from ..embeddings.client import EmbeddingError, ServerUnavailable
from ..embeddings.service import EmbeddingService, ModelMissingError
from ..picker import app_items, hit_key, run_pick
from ..search import hybrid
from ..search.queryd import QuerydClient

if TYPE_CHECKING:
    import argparse

_DEGRADE_ERRORS = (ModelMissingError, ServerUnavailable, EmbeddingError, OSError)


def _warn_degraded(exc: Exception) -> None:
    print(
        f"feagle: semantic search unavailable ({exc}); showing full-text results",
        file=sys.stderr,
    )


def _request_from_daemon(query: str, limit: int, mode: str) -> dict[str, Any] | None:
    client = QuerydClient()
    if not client.is_running():
        return None
    try:
        result = client.request("search", query=query, limit=limit, mode=mode)
    except (OSError, ValueError):
        return None
    return result if result.get("ok") else None


def search_in_process(cfg: dict[str, Any], query: str, limit: int, mode: str) -> dict[str, Any]:
    search_cfg = cfg["search"]
    stem = search_cfg["stemming"] != "none"
    started = time.perf_counter()
    conn = db.connect(paths.db_path(), read_only=True)
    service: EmbeddingService | None = None
    used = "fts"
    try:
        if mode in ("auto", "hybrid"):
            try:
                service = EmbeddingService(cfg)
                embedder = service.start()
            except _DEGRADE_ERRORS as exc:
                _warn_degraded(exc)
                service = None
                embedder = None

            if service is not None and db.has_vec_table(conn):
                vector = embedder.embed_queries([query])[0]
                hits = hybrid.search_hybrid(
                    conn,
                    query,
                    query_vector=vector,
                    limit=limit,
                    candidates=search_cfg["candidates"],
                    rrf_k=search_cfg["rrf_k"],
                    stem=stem,
                    max_distance=search_cfg["max_distance"],
                    max_per_file=search_cfg["max_per_file"],
                )
                used = "hybrid"
            else:
                hits = hybrid.search_text(
                    conn, query, limit=limit,
                    candidates=search_cfg["candidates"], rrf_k=search_cfg["rrf_k"],
                    stem=stem, max_per_file=search_cfg["max_per_file"],
                )
        else:
            hits = hybrid.search_text(
                conn, query, limit=limit,
                candidates=search_cfg["candidates"], rrf_k=search_cfg["rrf_k"],
                stem=stem, max_per_file=search_cfg["max_per_file"],
            )
    finally:
        conn.close()
        if service is not None:
            service.close()

    return {
        "ok": True,
        "mode": used,
        "took_ms": round((time.perf_counter() - started) * 1000, 1),
        "results": [
            {
                "rank": rank,
                "score": hit.score,
                "path": hit.path,
                "page": hit.page,
                "line": hit.line,
                "time": hit.time_ms / 1000 if hit.time_ms is not None else None,
                "snippet": hit.snippet,
                "chunk_id": hit.chunk_id,
            }
            for rank, hit in enumerate(hits, start=1)
        ],
    }


def search_best_effort(
    cfg: dict[str, Any], query: str, limit: int, mode: str
) -> dict[str, Any]:
    """Search through the running ``queryd`` when possible, else in-process.

    Both ``feagle search`` and ``feagle pick --query`` go through this so they
    share a warm daemon (and its hybrid embeddings) instead of each starting an
    embedder of their own.
    """
    result = _request_from_daemon(query, limit, mode)
    if result is None:
        result = search_in_process(cfg, query, limit, mode)
    return result


def _format_time(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _location(result: dict[str, Any]) -> str:
    location = result["path"]
    if result.get("line") is not None:
        location += f":{result['line']}"
    if result.get("page") is not None:
        location += f" (page {result['page']})"
    if result.get("time") is not None:
        location += f" @ {_format_time(result['time'])}"
    return location


def _pick_and_open(cfg: dict[str, Any], result: dict[str, Any], *, with_apps: bool) -> int:
    items, app_map = app_items() if with_apps else ([], {})
    hits_by_key: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(result.get("results") or []):
        key = hit_key(item, index)
        hits_by_key[key] = item
        items.append((key, f"{_location(item)}  {item['snippet']}"))
    return run_pick(cfg, items, app_map, hits_by_key=hits_by_key)


def run(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    if not paths.db_path().exists():
        print("feagle: no index yet; run 'feagle index' first.", file=sys.stderr)
        return 1

    mode = args.mode or cfg["search"]["mode"]
    limit = args.limit if args.limit is not None else cfg["search"]["limit"]

    result = search_best_effort(cfg, args.query, limit, mode)

    if args.pick:
        return _pick_and_open(cfg, result, with_apps=args.with_apps)

    if args.json:
        payload = {
            "query": args.query,
            "mode": result["mode"],
            "took_ms": result["took_ms"],
            "results": result["results"],
        }
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    elif result["results"]:
        for item in result["results"]:
            print(f"{item['rank']}. {_location(item)}")
            print(f"   {item['snippet']}")
    else:
        print("No results.")

    return 0
