"""Text extraction.

Phase 1 handles plain text, markdown and source code (any allowlisted
extension). PDF is a stub that raises a clear, catchable error; the real
``pdftotext`` extractor arrives in a later phase.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..log import get_logger

log = get_logger("indexer.extract")

#: Bytes inspected for a NUL byte when deciding whether a file is binary.
BINARY_SNIFF = 8192


class ExtractionError(Exception):
    """A file could not be read or decoded."""


class UnsupportedFile(Exception):
    """A file is intentionally not indexed (binary, or not implemented yet)."""


@dataclass(frozen=True)
class Extracted:
    """Extracted text.

    ``pages`` holds one string per logical page: a single entry for plain text,
    one entry per PDF page (in order). ``kind`` is ``"text"`` or ``"pdf"``.
    """

    pages: tuple[str, ...]
    kind: str


def extract(path: Path, *, max_bytes: int | None = None) -> Extracted:
    """Extract indexable text from ``path`` based on its extension."""
    if path.suffix.lower() == ".pdf":
        return extract_pdf(path)
    return extract_text(path, max_bytes=max_bytes)


def extract_text(path: Path, *, max_bytes: int | None = None) -> Extracted:
    data = _read_bytes(path, max_bytes)
    if b"\x00" in data[:BINARY_SNIFF]:
        raise UnsupportedFile(f"binary content: {path}")
    text = data.decode("utf-8", errors="replace")
    return Extracted(pages=(text,), kind="text")


def extract_pdf(path: Path) -> Extracted:
    """Placeholder for the ``pdftotext`` extractor (planned for a later phase)."""
    raise UnsupportedFile(f"PDF extraction is not implemented yet: {path}")


def _read_bytes(path: Path, max_bytes: int | None) -> bytes:
    chunks: list[bytes] = []
    total = 0
    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(1 << 20)
                if not chunk:
                    break
                total += len(chunk)
                if max_bytes is not None and total > max_bytes:
                    raise ExtractionError(f"file exceeds {max_bytes} bytes: {path}")
                chunks.append(chunk)
    except OSError as exc:
        raise ExtractionError(f"cannot read {path}: {exc}") from exc
    return b"".join(chunks)
