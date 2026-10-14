"""Stemming for the full-text index.

SQLite's FTS5 has no Russian stemmer, so word forms like ``книга`` / ``книги`` /
``книге`` would never match each other. We keep an extra FTS column holding a
stemmed copy of the text, produced with the Snowball Russian stemmer.

Only Cyrillic tokens are stemmed; everything else (Latin identifiers, code,
numbers) is left out of the stemmed column and continues to be matched by the
regular ``body`` column. This keeps the two rankings from duplicating each other
on English-only corpora.
"""

from __future__ import annotations

import re
from functools import lru_cache

_WORD = re.compile(r"\w+", re.UNICODE)
_CYRILLIC = re.compile(r"[\u0400-\u04FF]")

#: Words repeat heavily in natural text, so stemming the same token again is
#: wasted pure-Python work (it dominated indexing in the profiler).
_STEM_CACHE_SIZE = 65536

_stemmer: object | None = None
_loaded = False


def _get_stemmer():
    global _stemmer, _loaded
    if not _loaded:
        _loaded = True
        try:
            import snowballstemmer

            _stemmer = snowballstemmer.stemmer("russian")
        except Exception:  # noqa: BLE001 - stemming is best-effort
            _stemmer = None
    return _stemmer


def available() -> bool:
    """Whether a Russian stemmer is importable."""
    return _get_stemmer() is not None


def is_cyrillic(token: str) -> bool:
    return bool(_CYRILLIC.search(token))


@lru_cache(maxsize=_STEM_CACHE_SIZE)
def stem_token(token: str) -> str:
    """Stem a single Cyrillic token; return others unchanged (cached)."""
    stemmer = _get_stemmer()
    if stemmer is None or not is_cyrillic(token):
        return token
    return stemmer.stemWord(token.lower())  # type: ignore[attr-defined]


def stem_token_cache_info() -> object:
    """Cache statistics, so the benchmark can report how well it works."""
    return stem_token.cache_info()


def stem_text(text: str) -> str:
    """Return the stems of the Cyrillic words in ``text`` (others dropped)."""
    return " ".join(stem_token(word) for word in _WORD.findall(text) if is_cyrillic(word))


def stem_query_terms(terms: list[str]) -> list[str]:
    """Stem query terms, keeping only those the stem column can match.

    Latin terms are dropped: they are already handled by the ``body`` column,
    and including them here would double-count lexical matches.
    """
    result: list[str] = []
    for term in terms:
        if is_cyrillic(term):
            stem = stem_token(term)
            if stem and stem not in result:
                result.append(stem)
    return result
