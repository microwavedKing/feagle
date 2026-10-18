"""Status command: report index and embedding statistics."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

from .. import db, paths
from ..search.vec import vec_count

if TYPE_CHECKING:
    import argparse


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
        vectors = vec_count(conn) if db.has_vec_table(conn) else 0
        schema_version = db.get_schema_version(conn)
        embedding_dim = db.get_embedding_dim(conn)
        model_id = db.get_meta(conn, "model_id")
    finally:
        conn.close()

    files_total = sum(by_state.values())
    coverage = round(vectors / chunks, 4) if chunks else 0.0
    payload = {
        "database": str(db_path),
        "size_bytes": db_path.stat().st_size,
        "schema_version": schema_version,
        "files": {"total": files_total, **by_state},
        "chunks": chunks,
        "vectors": vectors,
        "vec_coverage": coverage,
        "embedding_dim": embedding_dim,
        "model_id": model_id,
    }

    if args.json:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        states = " ".join(f"{state}={count}" for state, count in sorted(by_state.items()))
        print(f"Database:  {db_path} (schema v{schema_version})")
        print(f"Files:     {files_total}" + (f" ({states})" if states else ""))
        print(f"Chunks:    {chunks}")
        print(f"Vectors:   {vectors} (coverage {coverage:.0%}, dim {embedding_dim})")
        if model_id:
            print(f"Model:     {model_id}")
        print(f"Size:      {payload['size_bytes']} bytes")

    return 0
