"""Text extraction.

Plain text, markdown and source code are read directly (any allowlisted
extension). PDF is extracted with ``pdftotext`` (poppler) run as a
subprocess: one string per page, split on the form feed (``\\f``) that poppler
emits between pages.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..log import get_logger
from .ocr import (
    DEFAULT_DPI,
    DEFAULT_LANGS,
    IMAGE_SUFFIXES,
    OCR_TIMEOUT,
    OcrError,
    image_ocr_available,
    ocr_image,
    ocr_pages,
)

log = get_logger("indexer.extract")

#: Bytes inspected for a NUL byte when deciding whether a file is binary.
BINARY_SNIFF = 8192

#: Wall-clock limit for a single ``pdftotext`` invocation.
PDF_TIMEOUT = 30.0

PDF_MISSING_HINT = "install poppler (pdftotext) to index PDF files"

IMAGE_DISABLED_HINT = "image OCR is off (set [index].ocr_images = true to index images)"
IMAGE_NO_TESSERACT_HINT = "install tesseract to index images"

#: Encodings tried (in order) when a text file is not valid UTF-8. cp1251 (the
#: common legacy Russian encoding) is only accepted when the result actually
#: looks Cyrillic, so Western European files fall through to cp1252/latin-1.
#: koi8-r is deliberately not listed: it cannot be told apart from cp1251
#: reliably, and guessing wrong would garble the file.
CYRILLIC_ENCODINGS = ("cp1251",)
WESTERN_ENCODINGS = ("cp1252", "latin-1")

#: A legacy decode is considered "Cyrillic" when at least this share of its
#: letters are Cyrillic.
CYRILLIC_RATIO = 0.5


class ExtractionError(Exception):
    """A file could not be read or decoded."""


class UnsupportedFile(Exception):
    """A file is intentionally not indexed (binary, or a missing tool)."""


@dataclass(frozen=True)
class Extracted:
    """Extracted text.

    ``pages`` holds one string per logical page: a single entry for plain text,
    one entry per PDF page (in order), and one entry for an OCR'd image. ``kind``
    is ``"text"``, ``"pdf"`` or ``"image"``. ``encoding`` records how a text file
    was decoded (``"utf-8"`` by default).
    """

    pages: tuple[str, ...]
    kind: str
    encoding: str = "utf-8"


def pdftotext_available() -> bool:
    """Whether the ``pdftotext`` binary is on ``PATH``."""
    return shutil.which("pdftotext") is not None


def extract(
    path: Path,
    *,
    max_bytes: int | None = None,
    ocr: bool = False,
    ocr_images: bool = False,
    ocr_langs: str = DEFAULT_LANGS,
    ocr_dpi: int = DEFAULT_DPI,
    ocr_min_chars: int = 20,
    ocr_max_pages: int = 200,
) -> Extracted:
    """Extract indexable text from ``path`` based on its extension."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf(
            path,
            ocr=ocr,
            ocr_langs=ocr_langs,
            ocr_dpi=ocr_dpi,
            ocr_min_chars=ocr_min_chars,
            ocr_max_pages=ocr_max_pages,
        )
    if suffix in IMAGE_SUFFIXES:
        return extract_image(
            path,
            enabled=ocr_images,
            ocr_langs=ocr_langs,
        )
    return extract_text(path, max_bytes=max_bytes)


def is_cyrillic_heavy(text: str, ratio: float = CYRILLIC_RATIO) -> bool:
    """Whether most alphabetic characters in ``text`` are Cyrillic."""
    letters = 0
    cyrillic = 0
    for char in text:
        if not char.isalpha():
            continue
        letters += 1
        if "\u0400" <= char <= "\u04ff":
            cyrillic += 1
    return letters > 0 and cyrillic / letters >= ratio


def decode_text(data: bytes) -> tuple[str, str]:
    """Decode file bytes, falling back to legacy encodings.

    Strict UTF-8 wins. Otherwise a Cyrillic legacy encoding is used when it
    decodes cleanly and the result is actually Cyrillic (cp1251 files are common
    in Russian text corpora); Western files fall through to cp1252/latin-1.
    """
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass

    for encoding in CYRILLIC_ENCODINGS:
        candidate = data.decode(encoding, errors="replace")
        if "\ufffd" not in candidate and is_cyrillic_heavy(candidate):
            return candidate, encoding

    for encoding in WESTERN_ENCODINGS:
        candidate = data.decode(encoding, errors="replace")
        if "\ufffd" not in candidate:
            return candidate, encoding

    return data.decode("utf-8", errors="replace"), "utf-8"


def extract_text(path: Path, *, max_bytes: int | None = None) -> Extracted:
    data = _read_bytes(path, max_bytes)
    if b"\x00" in data[:BINARY_SNIFF]:
        raise UnsupportedFile(f"binary content: {path}")
    text, encoding = decode_text(data)
    if encoding != "utf-8":
        log.info("decoded %s as %s", path, encoding)
    return Extracted(pages=(text,), kind="text", encoding=encoding)


def extract_image(
    path: Path,
    *,
    enabled: bool = False,
    ocr_langs: str = DEFAULT_LANGS,
    timeout: float = OCR_TIMEOUT,
) -> Extracted:
    """OCR an image file directly with ``tesseract``.

    Images need only ``tesseract`` (there is nothing to render), unlike scanned
    PDF pages which also need ``pdftoppm``. When image OCR is disabled in the
    config, or ``tesseract`` is missing, the file is skipped with a clear
    message instead of failing the run.
    """
    if not enabled:
        raise UnsupportedFile(f"{IMAGE_DISABLED_HINT}: {path}")
    if not image_ocr_available():
        raise UnsupportedFile(f"{IMAGE_NO_TESSERACT_HINT}: {path}")
    try:
        text = ocr_image(path, langs=ocr_langs, timeout=timeout)
    except OcrError as exc:
        raise ExtractionError(str(exc)) from exc
    if not text.strip():
        log.info("no text recognized in %s", path)
    return Extracted(pages=(text,), kind="image")


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
