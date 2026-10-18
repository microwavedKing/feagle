"""Hybrid search: fuse BM25 (exact and stemmed) with vector rankings via RRF.

RRF score of a document is ``sum over rankings of 1 / (k + rank)`` where
``rank`` is 1-based within each ranking. RRF needs only the ranks, so the
incompatible score scales of BM25 and cosine distance do not matter.

The exact FTS side is used with AND semantics only: an OR fallback (enabled for
pure full-text search) would let a single common word promote a document that
the semantic side did not consider relevant.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..config import DEFAULT_MAX_DISTANCE
from ..embeddings import presets
from .fts import SearchHit, _snippet, search_fts, search_fts_stemmed
from .vec import knn

if TYPE_CHECKING:
    import sqlite3

DEFAULT_RRF_K = 60


def effective_max_distance(cfg: dict[str, Any]) -> float:
    """The vector distance cutoff, falling back to the model's own scale.

    ``[search].max_distance`` is tuned on EmbeddingGemma. Another model can put
    its distances on a different scale (Qwen3-Embedding spreads even loosely
    related pairs much further apart), and a cutoff that is too tight silently
    drops *every* vector neighbour, leaving full-text-only results. When the
    config still holds the built-in default, the model preset's suggestion wins;
    an explicit user value is always respected.
    """
    configured = float(cfg["search"]["max_distance"])
    if configured != DEFAULT_MAX_DISTANCE:
        return configured
    spec = presets.spec_for(str(cfg["embeddings"]["model"]))
    suggested = spec.max_distance if spec is not None else None
    return float(suggested) if suggested is not None else configured


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
    from .. import db

    time_col = (
        "c.time_ms AS time_ms" if db.has_column(conn, "chunks", "time_ms") else "NULL AS time_ms"
    )
    placeholders = ",".join("?" * len(chunk_ids))
    rows = conn.execute(
        "SELECT c.id AS chunk_id, f.path AS path, c.page AS page, c.line AS line, "
        f"{time_col}, c.body AS body "
        f"FROM chunks c JOIN files f ON f.id = c.file_id WHERE c.id IN ({placeholders})",
        chunk_ids,
    ).fetchall()
    return {row["chunk_id"]: row for row in rows}


def _to_hits(
    conn: sqlite3.Connection,
    ranked: list[tuple[int, float]],
    limit: int,
    *,
    max_per_file: int = 0,
) -> list[SearchHit]:
    ordered_ids = [chunk_id for chunk_id, _ in ranked]
    details = _fetch_details(conn, ordered_ids)
    scores = dict(ranked)
    hits: list[SearchHit] = []
    per_file: dict[str, int] = {}
    for chunk_id in ordered_ids:
        row = details.get(chunk_id)
        if row is None:
            continue
        path = row["path"]
        if max_per_file and per_file.get(path, 0) >= max_per_file:
            continue
        per_file[path] = per_file.get(path, 0) + 1
        hits.append(
            SearchHit(
                chunk_id=chunk_id,
                score=scores[chunk_id],
                path=path,
                page=row["page"],
                line=row["line"],
                snippet=_snippet(row["body"]),
                time_ms=row["time_ms"],
            )
        )
        if len(hits) >= limit:
            break
    return hits


def _text_rankings(
    conn: sqlite3.Connection, query: str, candidates: int, *, stem: bool, fallback_or: bool
) -> list[list[int]]:
    exact = [
        hit.chunk_id
        for hit in search_fts(conn, query, limit=candidates, fallback_or=fallback_or)
    ]
    rankings = [exact] if exact else []
    if stem:
        stemmed = [
            hit.chunk_id
            for hit in search_fts_stemmed(conn, query, limit=candidates, fallback_or=fallback_or)
        ]
        if stemmed:
            rankings.append(stemmed)
    return rankings


def search_text(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = 20,
    candidates: int = 50,
    rrf_k: int = DEFAULT_RRF_K,
    stem: bool = True,
    max_per_file: int = 0,
) -> list[SearchHit]:
    """Full-text search fusing the exact and stemmed rankings."""
    rankings = _text_rankings(conn, query, candidates, stem=stem, fallback_or=True)
    if not rankings:
        return []
    return _to_hits(conn, rrf(rankings, k=rrf_k), limit, max_per_file=max_per_file)


def search_hybrid(
    conn: sqlite3.Connection,
    query: str,
    *,
    query_vector: list[float] | None,
    limit: int = 20,
    candidates: int = 50,
    rrf_k: int = DEFAULT_RRF_K,
    stem: bool = True,
    max_distance: float = 0.0,
    max_per_file: int = 0,
) -> list[SearchHit]:
    """Run exact + stemmed FTS and (when available) KNN, then fuse.

    ``max_distance`` drops vector neighbours that are too far away, so an
    unrelated query does not return a page of weak matches. With
    ``query_vector=None`` this degrades to a full-text search.
    """
    rankings = _text_rankings(conn, query, candidates, stem=stem, fallback_or=False)

    if query_vector is not None:
        vec_ids = [
            hit.chunk_id
            for hit in knn(conn, query_vector, k=candidates)
            if max_distance <= 0 or hit.distance <= max_distance
        ]
        if vec_ids:
            rankings.append(vec_ids)

    if not rankings:
        return []
    return _to_hits(conn, rrf(rankings, k=rrf_k), limit, max_per_file=max_per_file)
