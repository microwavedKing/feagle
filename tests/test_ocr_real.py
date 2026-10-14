"""Real OCR test against the committed scanned-PDF fixture.

Skipped when tesseract/poppler are not installed. The fixture is an image-only
PDF (no text layer), generated once from the text fixture and committed.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from feagle import config, db
from feagle.indexer import pipeline
from feagle.indexer.extract import extract_pdf
from feagle.indexer.ocr import ocr_available
from feagle.search.fts import search_fts

SCANNED = Path(__file__).parent / "fixtures" / "scanned.pdf"

pytestmark = pytest.mark.skipif(
    not ocr_available(), reason="OCR needs tesseract and pdftoppm"
)


def test_scanned_pdf_has_no_text_layer():
    result = extract_pdf(SCANNED)
    assert "".join(result.pages).strip() == ""  # nothing without OCR


def test_ocr_recovers_the_text():
    result = extract_pdf(SCANNED, ocr=True, ocr_min_chars=10, ocr_dpi=200, ocr_langs="eng")
    text = " ".join(result.pages).lower()
    assert "heading" in text
    assert "alpha bravo charlie delta" in text


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
