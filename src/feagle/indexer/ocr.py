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
from concurrent.futures import ProcessPoolExecutor, as_completed
from functools import lru_cache
from pathlib import Path

from ..log import get_logger

log = get_logger("indexer.ocr")

RENDER_TIMEOUT = 60.0
OCR_TIMEOUT = 120.0
DEFAULT_LANGS = "rus+eng"
DEFAULT_DPI = 300

#: Hard cap on OCR worker processes. Every worker renders its own page (a 300 DPI
#: A4 image is tens of MB once decoded) and runs its own ``tesseract``, so the
#: memory peak grows with the worker count. Two is as far as the 8 GB target
#: machine should go; more workers mostly fight over the same cores.
MAX_OCR_WORKERS = 2

#: ``[index].ocr_psm`` value that leaves the choice to feagle.
PSM_AUTO = "auto"
#: Page segmentation mode that also runs orientation/script detection (OSD).
OSD_PSM = 1
#: The tesseract language holding the OSD data (``tesseract-data-osd``).
OSD_LANG = "osd"

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


def _tesseract_command(
    binary: str, image: Path, langs: str, psm: int | None = None
) -> list[str]:
    """The ``tesseract`` argv used for both PDF pages and image files.

    ``--oem 1`` forces the LSTM engine and ``preserve_interword_spaces`` keeps
    the spacing of the recognized text, which reads better in snippets. ``--psm``
    is only added when a mode was chosen (tesseract's default is 3, "fully
    automatic page segmentation, but no OSD").
    """
    command = [
        binary, str(image), "stdout",
        "-l", langs,
        "--oem", "1",
        "-c", "preserve_interword_spaces=1",
    ]
    if psm is not None:
        command += ["--psm", str(psm)]
    return command


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


@lru_cache(maxsize=1)
def osd_available() -> bool:
    """Whether the OSD data is installed (needed for orientation detection).

    Cached: it costs one ``tesseract --list-langs`` and the answer cannot change
    while the process lives.
    """
    return OSD_LANG in tesseract_languages()


def resolve_psm(
    psm: str | int | None, *, osd: bool = False, langs: str = DEFAULT_LANGS
) -> tuple[int | None, str]:
    """Turn ``[index].ocr_psm`` / ``ocr_osd`` into ``(psm, langs)`` for tesseract.

    An explicit mode always wins. ``"auto"`` uses the tesseract default (``None``)
    unless OSD is requested *and* the OSD data is installed — then mode 1 is used
    with ``osd`` appended to the language list, which is what makes tesseract
    detect and undo a rotated scan.
    """
    if isinstance(psm, int):
        return psm, langs
    if osd:
        if osd_available():
            wanted = langs.split("+")
            if OSD_LANG not in wanted:
                langs = f"{langs}+{OSD_LANG}"
            return OSD_PSM, langs
        log.warning("osd data not installed; using the default page segmentation")
    return None, langs


def ocr_pdf_page(
    pdf: Path,
    page: int,
    *,
    langs: str = DEFAULT_LANGS,
    dpi: int = DEFAULT_DPI,
    timeout: float = OCR_TIMEOUT,
    psm: int | None = None,
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
                _tesseract_command(ocr_bin, image, langs, psm),
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
    psm: int | None = None,
    tesseract: str | None = None,
) -> str:
    """Recognize the text of an image file directly. Raises :class:`OcrError`."""
    ocr_bin = tesseract or shutil.which("tesseract")
    if ocr_bin is None:
        raise OcrError("OCR requires tesseract")

    try:
        proc = subprocess.run(
            _tesseract_command(ocr_bin, image, langs, psm),
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


def _process_pool(workers: int) -> ProcessPoolExecutor:
    """The default executor factory (a seam so tests can run tasks inline)."""
    return ProcessPoolExecutor(max_workers=workers)


def _ocr_page_task(payload: tuple[str, int, str, int, float, int | None]):
    """OCR one PDF page inside a worker process.

    Module level and taking a plain tuple so it can be pickled. Returns
    ``(page, text, error)`` — never raises, so one bad page cannot break the pool.
    """
    pdf, page, langs, dpi, timeout, psm = payload
    try:
        text = ocr_pdf_page(
            Path(pdf), page, langs=langs, dpi=dpi, timeout=timeout, psm=psm
        )
    except OcrError as exc:
        return page, None, str(exc)
    except Exception as exc:  # noqa: BLE001 - a worker must never take the pool down
        return page, None, f"{type(exc).__name__}: {exc}"
    return page, text, None


def _ocr_pages_parallel(
    pdf: Path,
    targets: list[int],
    *,
    langs: str,
    dpi: int,
    timeout: float,
    psm: int | None,
    workers: int,
    executor_factory,
) -> list[tuple[int, str | None, str | None]] | None:
    """OCR ``targets`` in a process pool; ``None`` means "stay sequential"."""
    payloads = [
        (str(pdf), index + 1, langs, dpi, timeout, psm) for index in targets
    ]
    results: list[tuple[int, str | None, str | None]] = []
    try:
        with executor_factory(workers) as pool:
            futures = {
                pool.submit(_ocr_page_task, payload): index
                for payload, index in zip(payloads, targets, strict=True)
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    _, text, error = future.result()
                except Exception as exc:  # noqa: BLE001 - one dead worker, not the run
                    results.append((index, None, f"{type(exc).__name__}: {exc}"))
                    continue
                results.append((index, text, error))
    except Exception as exc:  # noqa: BLE001 - no processes at all (rare): degrade
        log.warning("OCR process pool unavailable (%s); using one worker", exc)
        return None
    return results


def _ocr_pages_sequential(
    pdf: Path,
    targets: list[int],
    *,
    langs: str,
    dpi: int,
    timeout: float,
    psm: int | None,
    ocr_page,
) -> list[tuple[int, str | None, str | None]]:
    results: list[tuple[int, str | None, str | None]] = []
    for index in targets:
        try:
            text = ocr_page(pdf, index + 1, langs=langs, dpi=dpi, timeout=timeout, psm=psm)
        except OcrError as exc:
            results.append((index, None, str(exc)))
            continue
        results.append((index, text, None))
    return results


def ocr_pages(
    pdf: Path,
    page_texts: list[str],
    *,
    langs: str = DEFAULT_LANGS,
    dpi: int = DEFAULT_DPI,
    min_chars: int = 20,
    max_pages: int = 200,
    timeout: float = OCR_TIMEOUT,
    psm: int | None = None,
    workers: int = 1,
    ocr_page=None,
    executor_factory=_process_pool,
) -> tuple[list[str], int]:
    """OCR pages that have little text, in place. Returns (texts, count).

    With ``workers > 1`` the pages are recognized in a process pool (tesseract is
    single-threaded, so this is the only way to use more than one core). The
    worker count is capped at :data:`MAX_OCR_WORKERS`; results are written back by
    page, so the text stays in page order whatever order the workers finish in.

    ``ocr_page`` overrides the built-in page OCR (used by tests) and disables the
    pool, since a caller-supplied callable cannot be sent to another process.
    """
    if not ocr_available():
        log.warning("OCR requested but tesseract/pdftoppm are missing; skipping %s", pdf)
        return page_texts, 0

    short = [index for index, text in enumerate(page_texts) if len(text.strip()) < min_chars]
    targets = short[:max_pages]
    if len(short) > len(targets):
        log.info("OCR page limit (%d) reached for %s", max_pages, pdf)
    if not targets:
        return page_texts, 0

    workers = max(1, min(int(workers), MAX_OCR_WORKERS))
    results = None
    if workers > 1 and ocr_page is None:
        results = _ocr_pages_parallel(
            pdf, targets,
            langs=langs, dpi=dpi, timeout=timeout, psm=psm,
            workers=workers, executor_factory=executor_factory,
        )
    if results is None:
        results = _ocr_pages_sequential(
            pdf, targets, langs=langs, dpi=dpi, timeout=timeout, psm=psm,
            ocr_page=ocr_page or ocr_pdf_page,
        )

    done = 0
    for index, text, error in results:
        if error:
            log.warning("OCR failed for %s page %d: %s", pdf, index + 1, error)
            continue
        if text and text.strip():
            page_texts[index] = text
            done += 1
    return page_texts, done


def cleanup_stale_temp(prefix: str = "feagle-ocr-") -> None:
    """Best-effort removal of leftover OCR temp directories."""
    tmp_root = Path(tempfile.gettempdir())
    for entry in tmp_root.glob(f"{prefix}*"):
        with contextlib.suppress(OSError):
            shutil.rmtree(entry, ignore_errors=True)
