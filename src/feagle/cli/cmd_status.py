"""Status command: report index statistics."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .. import db, paths


def run(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    db_path = paths.db_path()
    if not db_path.exists():
        print("feagle: no index yet; run 'feagle index' first.", file=sys.stderr)
        return 1

    conn = db.connect(read_only=True)
    try:
        by_state = {
            row["state"]: row["n"]
            for row in conn.execute("SELECT state, COUNT(*) AS n FROM files GROUP BY state")
        }
        chunks = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
        schema_version = db.get_schema_version(conn)
    finally:
        conn.close()

    files_total = sum(by_state.values())
    payload = {
        "database": str(db_path),
        "size_bytes": db_path.stat().st_size,
        "schema_version": schema_version,
        "files": {"total": files_total, **by_state},
        "chunks": chunks,
    }

    if args.json:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        states = " ".join(f"{state}={count}" for state, count in sorted(by_state.items()))
        print(f"Database:  {db_path} (schema v{schema_version})")
        print(f"Files:     {files_total}" + (f" ({states})" if states else ""))
        print(f"Chunks:    {chunks}")
        print(f"Size:      {payload['size_bytes']} bytes")

    return 0
