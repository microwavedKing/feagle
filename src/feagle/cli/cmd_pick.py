"""``feagle pick``: one launcher window with applications and indexed files.

Applications come first, so typing an application name shows it above any file
that happens to match; selecting one launches it, selecting a file opens it.
Files are only filtered by the launcher itself (wofi/rofi filter the list on
their side), so this is keyword/fuzzy matching over paths and snippets; pass
``--query`` to rank the files semantically instead.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from .. import db, paths
from ..picker import app_items, run_pick
from .cmd_search import search_in_process

SNIPPET_CHARS = 120


def _snippet(body: str, limit: int = SNIPPET_CHARS) -> str:
    return " ".join(body.split())[:limit]


def run(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    items, app_map = ([], {})
    if not args.no_apps:
        items, app_map = app_items()

    hits_by_path: dict[str, dict[str, Any]] = {}

    if args.query:
        if not paths.db_path().exists():
            print(
                "feagle: no index yet; showing applications only "
                "(run 'feagle index' first).",
                file=sys.stderr,
            )
        else:
            limit = args.limit if args.limit is not None else cfg["search"]["limit"]
            result = search_in_process(cfg, args.query, limit, cfg["search"]["mode"])
            for item in result["results"]:
                hits_by_path.setdefault(item["path"], item)
                location = item["path"]
                if item.get("line"):
                    location += f":{item['line']}"
                items.append((item["path"], f"{location}  {item['snippet']}"))
    elif not args.no_files:
        if not paths.db_path().exists():
            print(
                "feagle: no index yet; showing applications only "
                "(run 'feagle index' first).",
                file=sys.stderr,
            )
        else:
            conn = db.connect(paths.db_path(), read_only=True)
            try:
                rows = db.list_indexed_files(conn, limit=args.files)
            finally:
                conn.close()
            for path, body in rows:
                items.append((path, f"{path}  {_snippet(body)}"))

    return run_pick(cfg, items, app_map, hits_by_path=hits_by_path)
