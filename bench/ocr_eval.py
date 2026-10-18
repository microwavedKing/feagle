"""OCR evaluation: throughput (worker processes) and quality (``--psm`` / DPI).

Only the committed synthetic fixtures are used. ``tests/fixtures/scanned.pdf`` is
an image-only PDF, so ``pdfunite`` can duplicate it into a multi-page "scan" to
measure OCR throughput, and its known text is the ground truth for the
character/word error rates. Personal files are never read.

    python bench/ocr_eval.py                 # human-readable table
    python bench/ocr_eval.py --json          # machine-readable
    python bench/ocr_eval.py --pages 8       # size of the synthetic scan
"""

from __future__ import annotations

import argparse
import contextlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from feagle.indexer import ocr

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
SCANNED_PDF = FIXTURES / "scanned.pdf"
SCANNED_PNG = FIXTURES / "scanned_page.png"

#: The text drawn into the synthetic fixture (see ``scanned.pdf``).
GROUND_TRUTH = "Page one heading Alpha bravo charlie delta in the first page."

DEFAULT_DPIS = (150, 200, 300)
DEFAULT_PSMS: tuple[int | None, ...] = (None, 1, 3, 6, 11)


def normalise(text: str) -> str:
    return " ".join(text.split()).strip()


def edit_distance(reference: str, hypothesis: str) -> int:
    """Levenshtein distance over characters."""
    previous = list(range(len(hypothesis) + 1))
    for i, ref_char in enumerate(reference, start=1):
        current = [i]
        for j, hyp_char in enumerate(hypothesis, start=1):
            current.append(min(
                previous[j] + 1,                    # deletion
                current[j - 1] + 1,                 # insertion
                previous[j - 1] + (ref_char != hyp_char),
            ))
        previous = current
    return previous[-1]


def cer(reference: str, hypothesis: str) -> float:
    reference, hypothesis = normalise(reference), normalise(hypothesis)
    if not reference:
        return 0.0 if not hypothesis else 1.0
    return edit_distance(reference, hypothesis) / len(reference)


def wer(reference: str, hypothesis: str) -> float:
    ref_words, hyp_words = normalise(reference).split(), normalise(hypothesis).split()
    if not ref_words:
        return 0.0 if not hyp_words else 1.0
    return edit_distance(" ".join(ref_words), " ".join(hyp_words)) / len(" ".join(ref_words))


def build_scan(pages: int, workdir: Path) -> Path:
    """A multi-page image-only PDF made by repeating the fixture."""
    pdfunite = shutil.which("pdfunite")
    if pdfunite is None:
        raise SystemExit("pdfunite (poppler) is required to build the synthetic scan")
    target = workdir / f"scan-{pages}.pdf"
    subprocess.run(
        [pdfunite, *([str(SCANNED_PDF)] * pages), str(target)],
        check=True, capture_output=True,
    )
    return target


def render(pdf: Path, dpi: int, workdir: Path, *, page: int = 1) -> Path:
    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm is None:
        raise SystemExit("pdftoppm (poppler) is required to render pages")
    base = workdir / f"page-{page}-{dpi}"
    subprocess.run(
        [pdftoppm, "-f", str(page), "-l", str(page), "-r", str(dpi), "-png", "-singlefile",
         str(pdf), str(base)],
        check=True, capture_output=True,
    )
    return base.with_suffix(".png")


def mean_confidence(image: Path, *, langs: str, psm: int | None) -> float | None:
    """Mean tesseract word confidence (``-1`` rows and empty words excluded)."""
    binary = shutil.which("tesseract")
    if binary is None:
        return None
    command = [binary, str(image), "stdout", "-l", langs, "--oem", "1", "tsv"]
    if psm is not None:
        command += ["--psm", str(psm)]
    proc = subprocess.run(command, capture_output=True, text=True, check=False)
    values: list[float] = []
    for row in proc.stdout.splitlines()[1:]:
        cells = row.split("\t")
        if len(cells) < 12 or cells[0] != "5" or not cells[11].strip():
            continue
        with contextlib.suppress(ValueError):
            values.append(float(cells[10]))
    if not values:
        return None
    return round(sum(values) / len(values), 1)


def measure_quality(pdf: Path, workdir: Path, *, langs: str, dpis, psms) -> list[dict]:
    rows: list[dict] = []
    for dpi in dpis:
        image = render(pdf, dpi, workdir)
        for psm in psms:
            started = time.perf_counter()
            text = ocr.ocr_pdf_page(pdf, 1, langs=langs, dpi=dpi, psm=psm)
            seconds = time.perf_counter() - started
            rows.append({
                "dpi": dpi,
                "psm": "auto" if psm is None else psm,
                "seconds": round(seconds, 3),
                "cer": round(cer(GROUND_TRUTH, text), 4),
                "wer": round(wer(GROUND_TRUTH, text), 4),
                "confidence": mean_confidence(image, langs=langs, psm=psm),
                "text": normalise(text)[:70],
            })
    return rows


def measure_throughput(pdf: Path, pages: int, *, langs: str, dpi: int) -> list[dict]:
    rows: list[dict] = []
    for workers in (1, ocr.MAX_OCR_WORKERS):
        started = time.perf_counter()
        _, done = ocr.ocr_pages(
            pdf, [""] * pages, langs=langs, dpi=dpi, min_chars=1, workers=workers
        )
        seconds = time.perf_counter() - started
        rows.append({
            "workers": workers,
            "pages": done,
            "seconds": round(seconds, 3),
            "seconds_per_page": round(seconds / pages, 3) if pages else 0.0,
        })
    base = rows[0]["seconds"]
    for row in rows:
        row["speedup"] = round(base / row["seconds"], 2) if row["seconds"] else 0.0
    return rows


def measure_osd(
    pdf: Path,
    workdir: Path,
    *,
    dpi: int,
    page: int = 1,
    reference: str | None = None,
) -> dict:
    """How OSD behaves on an upright, a 180°- and a 90°-rotated page.

    OSD only rotates when its orientation confidence is high enough, which needs
    a text-heavy page. `--osd-pdf`/`--osd-page` point the measurement at one, and
    the reference text then comes from `pdftotext` instead of the two-line fixture.
    """
    ffmpeg = shutil.which("ffmpeg")
    upright = render(pdf, dpi, workdir, page=page)
    truth = normalise(reference if reference is not None else GROUND_TRUTH)
    result: dict[str, Any] = {
        "pdf": pdf.name,
        "page": page,
        "dpi": dpi,
        "reference_words": len(truth.split()),
        "rotations": {},
        "note": "ffmpeg is required to build the rotated copies",
    }
    if ffmpeg is None:
        return result

    images: dict[str, Path] = {"upright": upright}
    for name, filter_chain in (
        ("rot180", "transpose=1,transpose=1"),
        ("rot90", "transpose=1"),
    ):
        target = workdir / f"{name}.png"
        subprocess.run(
            [ffmpeg, "-loglevel", "error", "-y", "-i", str(upright),
             "-vf", filter_chain, str(target)],
            check=True, capture_output=True,
        )
        images[name] = target

    measured: dict[str, dict[str, Any]] = {}
    for name, image in images.items():
        row: dict[str, Any] = {}
        for suffix, langs, psm in (("plain", "eng", 3), ("osd", "eng+osd", 1)):
            started = time.perf_counter()
            text = normalise(_tesseract_text(image, langs=langs, psm=psm))
            row[f"seconds_{suffix}"] = round(time.perf_counter() - started, 2)
            row[f"cer_{suffix}"] = round(cer(truth, text), 4)
            row[f"text_{suffix}"] = text[:60]
        measured[name] = row
    result["rotations"] = measured
    result["note"] = (
        "upright vs 180° vs 90°; 'plain' is psm 3 without OSD, 'osd' is psm 1 with "
        "eng+osd. A short page gives OSD nothing to be confident about."
    )
    return result


def _tesseract_text(image: Path, *, langs: str, psm: int | None) -> str:
    binary = shutil.which("tesseract")
    if binary is None:
        return ""
    proc = subprocess.run(
        ocr._tesseract_command(binary, image, langs, psm),
        capture_output=True, check=False,
    )
    return proc.stdout.decode("utf-8", errors="replace")


def _osd_reference(args: argparse.Namespace) -> str:
    """The page text from the PDF itself: ground truth for a custom --osd-pdf page."""
    pdftotext = shutil.which("pdftotext")
    if pdftotext is None:
        raise SystemExit("pdftotext (poppler) is required for --osd-pdf")
    proc = subprocess.run(
        [pdftotext, "-f", str(args.osd_page), "-l", str(args.osd_page),
         str(args.osd_pdf), "-"],
        capture_output=True, text=True, check=True,
    )
    return proc.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure OCR speed and quality.")
    parser.add_argument("--pages", type=int, default=6, help="pages in the synthetic scan")
    parser.add_argument("--langs", default="eng", help="tesseract languages")
    parser.add_argument("--dpi", type=int, default=300, help="DPI for the throughput run")
    parser.add_argument(
        "--osd-pdf", type=Path, default=None, metavar="FILE",
        help="measure OSD on this PDF instead of the two-line fixture",
    )
    parser.add_argument("--osd-page", type=int, default=1, help="page of --osd-pdf to measure")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = parser.parse_args(argv)

    if not (ocr.tesseract_available() and ocr.pdftoppm_available()):
        print("OCR needs tesseract and pdftoppm; nothing measured.", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="feagle-ocr-bench-") as tmp:
        workdir = Path(tmp)
        scan = build_scan(args.pages, workdir)
        report = {
            "pages": args.pages,
            "langs": args.langs,
            "ground_truth": GROUND_TRUTH,
            "throughput": measure_throughput(scan, args.pages, langs=args.langs, dpi=args.dpi),
            "quality": measure_quality(
                SCANNED_PDF, workdir, langs=args.langs,
                dpis=DEFAULT_DPIS, psms=DEFAULT_PSMS,
            ),
            "osd": measure_osd(
                (args.osd_pdf or SCANNED_PDF).expanduser(),
                workdir,
                dpi=args.dpi,
                page=args.osd_page if args.osd_pdf else 1,
                reference=_osd_reference(args) if args.osd_pdf else None,
            ),
        }

    if args.json:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0

    print(f"OCR: {report['pages']} synthetic pages, langs={report['langs']}")
    print("\nThroughput (rendering + tesseract):")
    print(f"  {'workers':>7}  {'seconds':>8}  {'s/page':>7}  {'speedup':>7}")
    for row in report["throughput"]:
        print(f"  {row['workers']:>7}  {row['seconds']:>8.2f}  "
              f"{row['seconds_per_page']:>7.3f}  {row['speedup']:>7.2f}")
    print("\nQuality (page 1, CER/WER against the known text):")
    print(f"  {'dpi':>4}  {'psm':>5}  {'seconds':>7}  {'CER':>6}  {'WER':>6}  {'conf':>5}")
    for row in report["quality"]:
        conf = "-" if row["confidence"] is None else f"{row['confidence']:.1f}"
        print(f"  {row['dpi']:>4}  {row['psm']!s:>5}  {row['seconds']:>7.3f}  "
              f"{row['cer']:>6.3f}  {row['wer']:>6.3f}  {conf:>5}")
    osd = report["osd"]
    print(f"\nOSD ({osd['pdf']} page {osd['page']}, {osd['reference_words']} reference words):")
    if not osd["rotations"]:
        print(f"  {osd['note']}")
        return 0
    print(f"  {'page':>8}  {'psm3 CER':>8}  {'osd CER':>7}  {'psm3 s':>7}  {'osd s':>6}")
    for name, row in osd["rotations"].items():
        print(f"  {name:>8}  {row['cer_plain']:>8.3f}  {row['cer_osd']:>7.3f}  "
              f"{row['seconds_plain']:>7.2f}  {row['seconds_osd']:>6.2f}")
    print(f"  {osd['note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
