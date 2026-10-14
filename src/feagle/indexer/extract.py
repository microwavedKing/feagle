"""Text extraction.

Plain text, markdown and source code are read directly (any allowlisted
extension). PDF is extracted with ``pdftotext`` (poppler-utils) run as a
subprocess: one string per page, split on the form feed (``\\f``) that poppler
emits between pages.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..log import get_logger
from .ocr import DEFAULT_DPI, DEFAULT_LANGS, ocr_pages

log = get_logger("indexer.extract")

#: Bytes inspected for a NUL byte when deciding whether a file is binary.
BINARY_SNIFF = 8192

#: Wall-clock limit for a single ``pdftotext`` invocation.
PDF_TIMEOUT = 30.0

PDF_MISSING_HINT = "install poppler-utils (pdftotext) to index PDF files"


class ExtractionError(Exception):
    """A file could not be read or decoded."""


class UnsupportedFile(Exception):
    """A file is intentionally not indexed (binary, or a missing tool)."""


@dataclass(frozen=True)
class Extracted:
    """Extracted text.

    ``pages`` holds one string per logical page: a single entry for plain text,
    one entry per PDF page (in order). ``kind`` is ``"text"`` or ``"pdf"``.
    """

    pages: tuple[str, ...]
    kind: str


def pdftotext_available() -> bool:
    """Whether the ``pdftotext`` binary is on ``PATH``."""
    return shutil.which("pdftotext") is not None


def extract(
    path: Path,
    *,
    max_bytes: int | None = None,
    ocr: bool = False,
    ocr_langs: str = DEFAULT_LANGS,
    ocr_dpi: int = DEFAULT_DPI,
    ocr_min_chars: int = 20,
    ocr_max_pages: int = 200,
) -> Extracted:
    """Extract indexable text from ``path`` based on its extension."""
    if path.suffix.lower() == ".pdf":
        return extract_pdf(
            path,
            ocr=ocr,
            ocr_langs=ocr_langs,
            ocr_dpi=ocr_dpi,
            ocr_min_chars=ocr_min_chars,
            ocr_max_pages=ocr_max_pages,
        )
    return extract_text(path, max_bytes=max_bytes)


def extract_text(path: Path, *, max_bytes: int | None = None) -> Extracted:
    data = _read_bytes(path, max_bytes)
    if b"\x00" in data[:BINARY_SNIFF]:
        raise UnsupportedFile(f"binary content: {path}")
    text = data.decode("utf-8", errors="replace")
    return Extracted(pages=(text,), kind="text")


def extract_pdf(
    path: Path,
    *,
    timeout: float = PDF_TIMEOUT,
    ocr: bool = False,
    ocr_langs: str = DEFAULT_LANGS,
    ocr_dpi: int = DEFAULT_DPI,
    ocr_min_chars: int = 20,
    ocr_max_pages: int = 200,
) -> Extracted:
    """Extract a PDF page by page using ``pdftotext`` (with optional OCR)."""
    binary = shutil.which("pdftotext")
    if binary is None:
        raise UnsupportedFile(f"{PDF_MISSING_HINT}: {path}")

    try:
        proc = subprocess.run(
            [binary, "-enc", "UTF-8", "-q", "--", str(path), "-"],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ExtractionError(f"pdftotext timed out after {timeout:.0f}s: {path}") from exc
    except OSError as exc:
        raise ExtractionError(f"cannot run pdftotext: {exc}") from exc

    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise ExtractionError(
            f"pdftotext failed ({proc.returncode}) for {path}: {detail[:200]}"
        )

    text = proc.stdout.decode("utf-8", errors="replace")
    pages = text.split("\f")
    if pages and pages[-1] == "":
        pages.pop()  # poppler terminates the last page with a form feed too
    if not pages:
        pages = [""]

    if ocr:
        pages, ocrned = ocr_pages(
            path,
            pages,
            langs=ocr_langs,
            dpi=ocr_dpi,
            min_chars=ocr_min_chars,
            max_pages=ocr_max_pages,
        )
        if ocrned:
            log.info("OCR recovered text on %d page(s) of %s", ocrned, path)

    return Extracted(pages=tuple(pages), kind="pdf")


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
