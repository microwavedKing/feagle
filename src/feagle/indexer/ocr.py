"""OCR support for scanned PDF pages and images.

Scanned PDFs have no text layer, so ``pdftotext`` returns almost nothing. For
such pages we render the page to a PNG with ``pdftoppm`` (poppler, already a
dependency for PDF support) and run ``tesseract`` on it. Image files
(``png/jpg/jpeg/webp/tiff``) are fed to ``tesseract`` directly — no rendering is
needed, so only ``tesseract`` is required for them.

Both are optional system dependencies: without them the files simply stay empty.

The Russian language is available through the ``tesseract-data-rus`` package;
OCR languages default to ``rus+eng``.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..log import get_logger

log = get_logger("indexer.ocr")

RENDER_TIMEOUT = 60.0
OCR_TIMEOUT = 120.0
DEFAULT_LANGS = "rus+eng"
DEFAULT_DPI = 300

#: Image extensions recognized directly (no rendering, tesseract only).
IMAGE_SUFFIXES: tuple[str, ...] = (".png", ".jpg", ".jpeg", ".webp", ".tiff", ".tif")


class OcrError(Exception):
    """A page or image could not be rendered or recognized."""


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def pdftoppm_available() -> bool:
    return shutil.which("pdftoppm") is not None


def image_ocr_available() -> bool:
    """Whether images can be OCR'd: only ``tesseract`` is needed (no renderer)."""
    return tesseract_available()


def ocr_available() -> bool:
    """Whether *PDF page* OCR can run: it needs both a renderer and ``tesseract``."""
    return tesseract_available() and pdftoppm_available()


def _tesseract_command(binary: str, image: Path, langs: str) -> list[str]:
    """The ``tesseract`` argv used for both PDF pages and image files.

    ``--oem 1`` forces the LSTM engine and ``preserve_interword_spaces`` keeps
    the spacing of the recognized text, which reads better in snippets.
    """
    return [
        binary, str(image), "stdout",
        "-l", langs,
        "--oem", "1",
        "-c", "preserve_interword_spaces=1",
    ]


def tesseract_languages(*, timeout: float = 10.0) -> set[str]:
    """Return the installed tesseract language codes (empty on failure)."""
    binary = shutil.which("tesseract")
    if binary is None:
        return set()
    try:
        proc = subprocess.run(
            [binary, "--list-langs"], capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    lines = proc.stdout.splitlines()
    # the first line is a header ("List of available languages ...")
    return {line.strip() for line in lines[1:] if line.strip()}


def ocr_pdf_page(
    pdf: Path,
    page: int,
    *,
    langs: str = DEFAULT_LANGS,
    dpi: int = DEFAULT_DPI,
    timeout: float = OCR_TIMEOUT,
    pdftoppm: str | None = None,
    tesseract: str | None = None,
) -> str:
    """Recognize the text of a single PDF page. Raises :class:`OcrError`."""
    render_bin = pdftoppm or shutil.which("pdftoppm")
    ocr_bin = tesseract or shutil.which("tesseract")
    if render_bin is None or ocr_bin is None:
        raise OcrError("OCR requires both pdftoppm and tesseract")

    with tempfile.TemporaryDirectory(prefix="feagle-ocr-") as tmp:
        base = Path(tmp) / "page"
        try:
            render = subprocess.run(
                [
                    render_bin, "-f", str(page), "-l", str(page),
                    "-r", str(dpi), "-png", "-singlefile", str(pdf), str(base),
                ],
                capture_output=True,
                timeout=RENDER_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OcrError(f"pdftoppm failed for {pdf} page {page}: {exc}") from exc
        if render.returncode != 0:
            detail = render.stderr.decode("utf-8", errors="replace").strip()
            raise OcrError(f"pdftoppm exited {render.returncode}: {detail[:200]}")

        image = base.with_suffix(".png")
        if not image.exists():
            raise OcrError(f"pdftoppm produced no image for {pdf} page {page}")

        try:
            proc = subprocess.run(
                _tesseract_command(ocr_bin, image, langs),
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OcrError(f"tesseract failed for {pdf} page {page}: {exc}") from exc
        if proc.returncode != 0:
            detail = proc.stderr.decode("utf-8", errors="replace").strip()
            raise OcrError(f"tesseract exited {proc.returncode}: {detail[:200]}")

    return proc.stdout.decode("utf-8", errors="replace")


def ocr_image(
    image: Path,
    *,
    langs: str = DEFAULT_LANGS,
    timeout: float = OCR_TIMEOUT,
    tesseract: str | None = None,
) -> str:
    """Recognize the text of an image file directly. Raises :class:`OcrError`."""
    ocr_bin = tesseract or shutil.which("tesseract")
    if ocr_bin is None:
        raise OcrError("OCR requires tesseract")

    try:
        proc = subprocess.run(
            _tesseract_command(ocr_bin, image, langs),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OcrError(f"tesseract failed for {image}: {exc}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise OcrError(f"tesseract exited {proc.returncode}: {detail[:200]}")

    return proc.stdout.decode("utf-8", errors="replace")


def ocr_pages(
    pdf: Path,
    page_texts: list[str],
    *,
    langs: str = DEFAULT_LANGS,
    dpi: int = DEFAULT_DPI,
    min_chars: int = 20,
    max_pages: int = 200,
    timeout: float = OCR_TIMEOUT,
    ocr_page=ocr_pdf_page,
) -> tuple[list[str], int]:
    """OCR pages that have little text, in place. Returns (texts, count)."""
    if not ocr_available():
        log.warning("OCR requested but tesseract/pdftoppm are missing; skipping %s", pdf)
        return page_texts, 0

    done = 0
    for index, text in enumerate(page_texts):
        if done >= max_pages:
            log.info("OCR page limit (%d) reached for %s", max_pages, pdf)
            break
        if len(text.strip()) >= min_chars:
            continue
        try:
            recognized = ocr_page(pdf, index + 1, langs=langs, dpi=dpi, timeout=timeout)
        except OcrError as exc:
            log.warning("OCR failed for %s page %d: %s", pdf, index + 1, exc)
            continue
        if recognized.strip():
            page_texts[index] = recognized
            done += 1
    return page_texts, done


def cleanup_stale_temp(prefix: str = "feagle-ocr-") -> None:
    """Best-effort removal of leftover OCR temp directories."""
    tmp_root = Path(tempfile.gettempdir())
    for entry in tmp_root.glob(f"{prefix}*"):
        with contextlib.suppress(OSError):
            shutil.rmtree(entry, ignore_errors=True)
