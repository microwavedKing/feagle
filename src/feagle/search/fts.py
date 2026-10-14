"""Full-text search over the FTS5 index (BM25).

User queries are sanitized: every term is wrapped in double quotes so that FTS5
operators the user may type (``OR``, ``NEAR``, ``-``, ``*``, ``"``) cannot break
the query or change its meaning. A first pass ANDs the terms; if that yields
nothing, a second pass ORs them.
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
       c.body AS body
FROM chunks_fts
JOIN chunks c ON c.id = chunks_fts.chunk_id
JOIN files f ON f.id = c.file_id
WHERE chunks_fts MATCH ?
ORDER BY score ASC
LIMIT ?
"""


@dataclass(frozen=True)
class SearchHit:
    chunk_id: int
    score: float
    path: str
    page: int | None
    line: int | None
    snippet: str


def fts_terms(query: str) -> list[str]:
    """Split a query into safe FTS5 terms (quotes stripped, empties dropped)."""
    return [term for token in query.split() if (term := token.replace('"', "").strip())]


def _match_expression(terms: list[str], operator: str) -> str:
    quoted = [f'"{term}"' for term in terms]
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
        )
        for row in rows
    ]


def _snippet(body: str, limit: int = SNIPPET_CHARS) -> str:
    flat = " ".join(body.split())
    return flat[:limit]


def search_fts(conn: sqlite3.Connection, query: str, *, limit: int = 20) -> list[SearchHit]:
    """Return BM25-ranked hits for ``query`` (AND first, then OR)."""
    terms = fts_terms(query)
    if not terms or limit <= 0:
        return []

    rows = conn.execute(_SELECT, (_match_expression(terms, "AND"), limit)).fetchall()
    if not rows and len(terms) > 1:
        rows = conn.execute(_SELECT, (_match_expression(terms, "OR"), limit)).fetchall()
    return _rows_to_hits(rows)
