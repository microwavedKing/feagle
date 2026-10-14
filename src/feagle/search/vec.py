"""Vector search over the ``chunks_vec`` table (sqlite-vec ``vec0``).

KNN is brute force (no ANN index); on the target corpus (~100k chunks) this is
a few tens of milliseconds. Deletions are done one row at a time, which is the
reliable path for vec0, and do not shrink the file immediately (``--rebuild``
reclaims space).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import sqlite_vec


@dataclass(frozen=True)
class VectorHit:
    chunk_id: int
    distance: float


def insert_vectors(
    conn: sqlite3.Connection, items: list[tuple[int, list[float]]]
) -> int:
    """Insert ``(chunk_id, vector)`` pairs. Caller must delete stale rows first."""
    for chunk_id, vector in items:
        conn.execute(
            "INSERT INTO chunks_vec(chunk_id, embedding) VALUES (?, ?)",
            (chunk_id, sqlite_vec.serialize_float32(vector)),
        )
    return len(items)


def delete_vectors(conn: sqlite3.Connection, chunk_ids: list[int]) -> None:
    for chunk_id in chunk_ids:
        conn.execute("DELETE FROM chunks_vec WHERE chunk_id = ?", (chunk_id,))


def clear_vectors(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM chunks_vec")


def knn(conn: sqlite3.Connection, vector: list[float], *, k: int = 50) -> list[VectorHit]:
    """Return the ``k`` nearest chunks, closest first."""
    if k <= 0:
        return []
    rows = conn.execute(
        "SELECT chunk_id, distance FROM chunks_vec WHERE embedding MATCH ? AND k = ? "
        "ORDER BY distance",
        (sqlite_vec.serialize_float32(vector), k),
    ).fetchall()
    return [VectorHit(chunk_id=row["chunk_id"], distance=float(row["distance"])) for row in rows]


def vec_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM chunks_vec").fetchone()
    return int(row["n"]) if row is not None else 0
