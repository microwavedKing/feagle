"""Chunking: split extracted text into index-sized pieces.

Chunks are measured in characters (rough proxy for tokens: ~300-500 tokens
~= 1200-1600 characters). Boundaries prefer paragraphs, then lines, then
spaces. Consecutive chunks overlap so that text spanning a boundary is not
lost. PDF pages are never merged: each chunk belongs to exactly one page.
"""

from __future__ import annotations

from dataclasses import dataclass

from .extract import Extracted

#: Default sizing, in characters.
TARGET_CHARS = 1400
OVERLAP_CHARS = 150
MIN_CHARS = 200
MAX_CHARS = 2200


@dataclass(frozen=True)
class Chunk:
    """A piece of a file, with its source position."""

    seq: int
    page: int | None
    line: int | None
    body: str


def _find_break(text: str, start: int, end: int, target: int) -> int:
    """Return the end offset for a chunk, preferring a natural break."""
    floor = start + max(target // 2, 1)
    for sep in ("\n\n", "\n", " "):
        index = text.rfind(sep, floor, end)
        if index != -1:
            return index + len(sep)
    return end


def _enforce_max(spans: list[tuple[int, int]], max_chars: int) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    for start, end in spans:
        while end - start > max_chars:
            result.append((start, start + max_chars))
            start += max_chars
        result.append((start, end))
    return result


def _merge_tiny(
    spans: list[tuple[int, int]], min_chars: int, max_chars: int
) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and end - start < min_chars:
            prev_start, _ = merged[-1]
            if end - prev_start <= max_chars:
                merged[-1] = (prev_start, end)
                continue
        merged.append((start, end))
    return merged


def _spans(
    text: str,
    *,
    target: int,
    overlap: int,
    min_chars: int,
    max_chars: int,
) -> list[tuple[int, int]]:
    n = len(text)
    if n == 0:
        return []

    spans: list[tuple[int, int]] = []
    pos = 0
    while pos < n:
        start = pos
        end = min(start + target, n)
        if end < n:
            end = _find_break(text, start, end, target)
        spans.append((start, end))
        if end >= n:
            break
        # Step back by the overlap, but snap forward to a line boundary so the
        # next chunk starts exactly at the beginning of a source line (this is
        # what makes ``Chunk.line`` precise). The previous chunk ends on a line
        # boundary too, so snapping never leaves a gap.
        next_pos = max(end - overlap, start + 1)
        line_break = text.find("\n", next_pos)
        if line_break != -1 and line_break + 1 <= end:
            next_pos = line_break + 1
        pos = next_pos

    spans = _merge_tiny(spans, min_chars, max_chars)
    return _enforce_max(spans, max_chars)


def chunk_extracted(
    extracted: Extracted,
    *,
    target: int = TARGET_CHARS,
    overlap: int = OVERLAP_CHARS,
    min_chars: int = MIN_CHARS,
    max_chars: int = MAX_CHARS,
) -> list[Chunk]:
    """Split ``extracted`` into ordered chunks."""
    chunks: list[Chunk] = []
    seq = 0
    if extracted.kind == "pdf":
        for page_number, page_text in enumerate(extracted.pages, start=1):
            for start, end in _spans(
                page_text, target=target, overlap=overlap, min_chars=min_chars, max_chars=max_chars
            ):
                chunk = Chunk(seq=seq, page=page_number, line=None, body=page_text[start:end])
                chunks.append(chunk)
                seq += 1
        return chunks

    text = extracted.pages[0] if extracted.pages else ""
    for start, end in _spans(
        text, target=target, overlap=overlap, min_chars=min_chars, max_chars=max_chars
    ):
        # An OCR'd image has no meaningful source line; only text files do.
        line = text.count("\n", 0, start) + 1 if extracted.kind == "text" else None
        chunks.append(Chunk(seq=seq, page=None, line=line, body=text[start:end]))
        seq += 1
    return chunks
