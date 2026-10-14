"""Hybrid search: fuse BM25 and vector rankings with Reciprocal Rank Fusion.

RRF score of a document is ``sum over rankings of 1 / (k + rank)`` where
``rank`` is 1-based within each ranking. RRF needs only the ranks, so the
incompatible score scales of BM25 and cosine distance do not matter.

The FTS side is used with AND semantics only: an OR fallback (enabled for pure
full-text search) would let a single common word promote a document that the
semantic side did not consider relevant.
"""

from __future__ import annotations

import sqlite3

from .fts import SearchHit, _snippet, search_fts
from .vec import knn

DEFAULT_RRF_K = 60


def rrf(rankings: list[list[int]], *, k: int = DEFAULT_RRF_K) -> list[tuple[int, float]]:
    """Fuse ranked chunk-id lists into ``[(chunk_id, score)]``, best first."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def _fetch_details(conn: sqlite3.Connection, chunk_ids: list[int]) -> dict[int, sqlite3.Row]:
    if not chunk_ids:
        return {}
    placeholders = ",".join("?" * len(chunk_ids))
    rows = conn.execute(
        "SELECT c.id AS chunk_id, f.path AS path, c.page AS page, c.line AS line, c.body AS body "
        f"FROM chunks c JOIN files f ON f.id = c.file_id WHERE c.id IN ({placeholders})",
        chunk_ids,
    ).fetchall()
    return {row["chunk_id"]: row for row in rows}


def _to_hits(
    conn: sqlite3.Connection, ranked: list[tuple[int, float]], limit: int
) -> list[SearchHit]:
    ordered_ids = [chunk_id for chunk_id, _ in ranked]
    details = _fetch_details(conn, ordered_ids)
    scores = dict(ranked)
    hits: list[SearchHit] = []
    for chunk_id in ordered_ids:
        row = details.get(chunk_id)
        if row is None:
            continue
        hits.append(
            SearchHit(
                chunk_id=chunk_id,
                score=scores[chunk_id],
                path=row["path"],
                page=row["page"],
                line=row["line"],
                snippet=_snippet(row["body"]),
            )
        )
        if len(hits) >= limit:
            break
    return hits


def search_hybrid(
    conn: sqlite3.Connection,
    query: str,
    *,
    query_vector: list[float] | None,
    limit: int = 20,
    candidates: int = 50,
    rrf_k: int = DEFAULT_RRF_K,
) -> list[SearchHit]:
    """Run FTS and (when available) KNN, fuse them and return top ``limit``.

    With ``query_vector=None`` this degrades to a pure FTS search.
    """
    fts_ids = [
        hit.chunk_id
        for hit in search_fts(conn, query, limit=candidates, fallback_or=False)
    ]

    vec_ids: list[int] = []
    if query_vector is not None:
        vec_ids = [hit.chunk_id for hit in knn(conn, query_vector, k=candidates)]

    if not fts_ids and not vec_ids:
        return []

    ranked = rrf([fts_ids, vec_ids], k=rrf_k)
    return _to_hits(conn, ranked, limit)
