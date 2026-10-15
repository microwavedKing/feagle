"""Full-text search over the FTS5 index (BM25).

The index has two text columns: ``body`` (the exact chunk text) and ``stem``
(Russian stems; see :mod:`feagle.stemming`). ``search_fts`` matches the exact
column; ``search_fts_stemmed`` matches the stemmed one so that different word
forms find each other. Callers combine both rankings with RRF (see
:mod:`feagle.search.hybrid`).

User queries are sanitized: every term is wrapped in double quotes so FTS5
operators the user may type (``OR``, ``NEAR``, ``-``, ``*``, ``"``) cannot break
the query or change its meaning.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

SNIPPET_CHARS = 200

_SELECT = """
SELECT c.id AS chunk_id,
       bm25(chunks_fts) AS score,
       f.path AS path,
       c.page AS page,
       c.line AS line,
       c.time_ms AS time_ms,
       c.body AS body
FROM chunks_fts
JOIN chunks c ON c.id = chunks_fts.chunk_id
JOIN files f ON f.id = c.file_id
WHERE chunks_fts MATCH ?
ORDER BY score ASC
LIMIT ?
"""

#: Same query without ``time_ms`` (an index not yet migrated to schema v3).
_SELECT_LEGACY = _SELECT.replace("c.time_ms AS time_ms", "NULL AS time_ms")


@dataclass(frozen=True)
class SearchHit:
    chunk_id: int
    score: float
    path: str
    page: int | None
    line: int | None
    snippet: str
    time_ms: int | None = None


def fts_terms(query: str) -> list[str]:
    """Split a query into safe FTS5 terms (quotes stripped, empties dropped)."""
    return [term for token in query.split() if (term := token.replace('"', "").strip())]


def _match_expression(terms: list[str], operator: str, column: str) -> str:
    quoted = [f'{column}:"{term}"' for term in terms]
    return f" {operator} ".join(quoted)


def _rows_to_hits(rows: list[sqlite3.Row]) -> list[SearchHit]:
    return [
        SearchHit(
            chunk_id=row["chunk_id"],
            score=float(row["score"]),
            path=row["path"],
            page=row["page"],
            line=row["line"],
            snippet=_snippet(row["body"]),
            time_ms=row["time_ms"],
        )
        for row in rows
    ]


def _snippet(body: str, limit: int = SNIPPET_CHARS) -> str:
    flat = " ".join(body.split())
    return flat[:limit]


def _select_sql(conn: sqlite3.Connection) -> str:
    from .. import db

    return _SELECT if db.has_column(conn, "chunks", "time_ms") else _SELECT_LEGACY


def _query(
    conn: sqlite3.Connection, terms: list[str], *, column: str, limit: int, fallback_or: bool
) -> list[sqlite3.Row]:
    select = _select_sql(conn)
    rows = conn.execute(select, (_match_expression(terms, "AND", column), limit)).fetchall()
    if not rows and fallback_or and len(terms) > 1:
        rows = conn.execute(select, (_match_expression(terms, "OR", column), limit)).fetchall()
    return rows


def search_fts(
    conn: sqlite3.Connection, query: str, *, limit: int = 20, fallback_or: bool = True
) -> list[SearchHit]:
    """Exact full-text search over the ``body`` column.

    With ``fallback_or`` the search retries an OR of the terms when the AND finds
    nothing (good for a pure full-text index). Hybrid search passes
    ``fallback_or=False``: there the vector side already supplies recall, and an
    OR on a single common word would inject noise into the fusion.
    """
    terms = fts_terms(query)
    if not terms or limit <= 0:
        return []
    return _rows_to_hits(_query(conn, terms, column="body", limit=limit, fallback_or=fallback_or))


def search_fts_stemmed(
    conn: sqlite3.Connection, query: str, *, limit: int = 20, fallback_or: bool = True
) -> list[SearchHit]:
    """Stemmed search over the ``stem`` column (Russian morphology)."""
    from ..stemming import available, stem_query_terms

    if not available():
        return []
    terms = stem_query_terms(fts_terms(query))
    if not terms or limit <= 0:
        return []
    return _rows_to_hits(_query(conn, terms, column="stem", limit=limit, fallback_or=fallback_or))
