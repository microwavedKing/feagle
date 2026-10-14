"""Search command: hybrid (semantic + full-text) with graceful degradation."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from .. import db, launchers, paths
from ..embeddings.client import EmbeddingError, ServerUnavailable
from ..embeddings.service import EmbeddingService, ModelMissingError
from ..openers import OpenError, open_result
from ..search import hybrid
from ..search.queryd import QuerydClient

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


def _search_in_process(cfg: dict[str, Any], query: str, limit: int, mode: str) -> dict[str, Any]:
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
                )
                used = "hybrid"
            else:
                hits = hybrid.search_text(
                    conn, query, limit=limit,
                    candidates=search_cfg["candidates"], rrf_k=search_cfg["rrf_k"], stem=stem,
                )
        else:
            hits = hybrid.search_text(
                conn, query, limit=limit,
                candidates=search_cfg["candidates"], rrf_k=search_cfg["rrf_k"], stem=stem,
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
                "snippet": hit.snippet,
                "chunk_id": hit.chunk_id,
            }
            for rank, hit in enumerate(hits, start=1)
        ],
    }


def _location(result: dict[str, Any]) -> str:
    location = result["path"]
    if result.get("line") is not None:
        location += f":{result['line']}"
    if result.get("page") is not None:
        location += f" (page {result['page']})"
    return location


def _pick_and_open(cfg: dict[str, Any], result: dict[str, Any]) -> int:
    results = result.get("results") or []
    if not results:
        return 0
    items = [
        (item["path"], f"{_location(item)}  {item['snippet']}") for item in results
    ]
    choice = launchers.pick(items, program=cfg["launcher"]["program"])
    if choice is None:
        return 0

    hit = next((item for item in results if item["path"] == choice), None)
    if hit is None:
        return 0
    try:
        open_result(
            cfg["openers"], Path(hit["path"]), line=hit.get("line"), page=hit.get("page")
        )
    except OpenError as exc:
        print(f"feagle: {exc}", file=sys.stderr)
        return 1
    return 0


def run(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    if not paths.db_path().exists():
        print("feagle: no index yet; run 'feagle index' first.", file=sys.stderr)
        return 1

    mode = args.mode or cfg["search"]["mode"]
    limit = args.limit if args.limit is not None else cfg["search"]["limit"]

    result = _request_from_daemon(args.query, limit, mode)
    if result is None:
        result = _search_in_process(cfg, args.query, limit, mode)

    if args.pick:
        return _pick_and_open(cfg, result)

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
