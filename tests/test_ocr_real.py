"""Real OCR tests against the committed fixtures.

Skipped when the needed binaries are not installed. ``scanned.pdf`` is an
image-only PDF (no text layer); ``scanned_page.png`` is page 1 of it rendered to
a PNG. OCR of image files needs only ``tesseract``; OCR of PDF pages also needs
``pdftoppm`` (poppler).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from feagle import config, db
from feagle.indexer import pipeline
from feagle.indexer.extract import extract, extract_pdf
from feagle.indexer.ocr import image_ocr_available, ocr_available
from feagle.search.fts import search_fts

FIXTURES = Path(__file__).parent / "fixtures"
SCANNED = FIXTURES / "scanned.pdf"
SCANNED_PNG = FIXTURES / "scanned_page.png"

ocr_pdf = pytest.mark.skipif(not ocr_available(), reason="OCR needs tesseract and pdftoppm")
ocr_image = pytest.mark.skipif(not image_ocr_available(), reason="OCR needs tesseract")


@ocr_pdf
def test_scanned_pdf_has_no_text_layer():
    result = extract_pdf(SCANNED)
    assert "".join(result.pages).strip() == ""  # nothing without OCR


@ocr_pdf
def test_ocr_recovers_the_text():
    result = extract_pdf(SCANNED, ocr=True, ocr_min_chars=10, ocr_dpi=200, ocr_langs="eng")
    text = " ".join(result.pages).lower()
    assert "heading" in text
    assert "alpha bravo charlie delta" in text


@ocr_pdf
def test_ocr_via_the_index_and_search(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    shutil.copy(SCANNED, docs / "scanned.pdf")

    cfg = config.default_config()
    cfg["index"]["roots"] = [str(docs)]
    cfg["index"]["ocr"] = True
    cfg["index"]["ocr_langs"] = "eng"
    cfg["index"]["ocr_min_chars"] = 10

    conn = db.open_database(tmp_path / "index.sqlite")
    try:
        stats = pipeline.index_roots(conn, cfg)
        assert stats.reindexed == 1
        hits = search_fts(conn, "charlie")
        assert hits and hits[0].path.endswith("scanned.pdf")
    finally:
        conn.close()


@ocr_image
def test_image_ocr_recovers_text():
    result = extract(SCANNED_PNG, ocr_images=True, ocr_langs="eng")
    assert result.kind == "image"
    text = " ".join(result.pages).lower()
    assert "heading" in text
    assert "alpha bravo charlie delta" in text


@ocr_image
def test_image_via_the_index_and_search(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    shutil.copy(SCANNED_PNG, docs / "shot.png")

    cfg = config.default_config()
    cfg["index"]["roots"] = [str(docs)]
    cfg["index"]["ocr_images"] = True
    cfg["index"]["ocr_langs"] = "eng"

    conn = db.open_database(tmp_path / "index.sqlite")
    try:
        stats = pipeline.index_roots(conn, cfg)
        assert stats.reindexed == 1
        hits = search_fts(conn, "charlie")
        assert hits and hits[0].path.endswith("shot.png")
    finally:
        conn.close()
