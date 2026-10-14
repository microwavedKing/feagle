"""Tests for the OCR fallback (no real tesseract required)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from feagle.indexer import ocr


def _which(names):
    return lambda name: f"/usr/bin/{name}" if name in names else None


def test_availability(monkeypatch):
    monkeypatch.setattr(ocr.shutil, "which", _which({"tesseract", "pdftoppm"}))
    assert ocr.ocr_available() is True
    monkeypatch.setattr(ocr.shutil, "which", _which({"pdftoppm"}))
    assert ocr.ocr_available() is False


def test_tesseract_languages(monkeypatch):
    monkeypatch.setattr(ocr.shutil, "which", _which({"tesseract"}))

    class Proc:
        stdout = "List of available languages in /usr/share/tessdata (3):\neng\nrus\nosd\n"
        returncode = 0

    monkeypatch.setattr(ocr.subprocess, "run", lambda *a, **k: Proc())
    assert ocr.tesseract_languages() == {"eng", "rus", "osd"}


def test_tesseract_languages_without_binary(monkeypatch):
    monkeypatch.setattr(ocr.shutil, "which", _which(set()))
    assert ocr.tesseract_languages() == set()


def test_ocr_pdf_page_builds_expected_commands(tmp_path, monkeypatch):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.7")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0].endswith("pdftoppm"):
            # simulate the PNG that pdftoppm would write
            Path(cmd[-1]).with_suffix(".png").write_bytes(b"PNG")
            return subprocess.CompletedProcess(cmd, 0, b"", b"")
        return subprocess.CompletedProcess(cmd, 0, b"recognized text\n", b"")

    monkeypatch.setattr(ocr.subprocess, "run", fake_run)

    text = ocr.ocr_pdf_page(
        pdf, 2, langs="rus+eng", dpi=200, pdftoppm="pdftoppm", tesseract="tesseract"
    )
    assert text == "recognized text\n"

    render = calls[0]
    assert render[0] == "pdftoppm"
    assert render[render.index("-f") + 1] == "2"
    assert render[render.index("-r") + 1] == "200"
    image = str(Path(render[-1]).with_suffix(".png"))
    assert calls[1] == ["tesseract", image, "stdout", "-l", "rus+eng"]


def test_ocr_pdf_page_render_failure(tmp_path, monkeypatch):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.7")
    monkeypatch.setattr(
        ocr.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, b"", b"boom"),
    )
    with pytest.raises(ocr.OcrError, match="pdftoppm"):
        ocr.ocr_pdf_page(pdf, 1, pdftoppm="pdftoppm", tesseract="tesseract")


def test_ocr_pdf_page_missing_binaries(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr.shutil, "which", _which(set()))
    with pytest.raises(ocr.OcrError, match="pdftoppm and tesseract"):
        ocr.ocr_pdf_page(tmp_path / "x.pdf", 1)


def test_ocr_pages_only_processes_short_pages(monkeypatch):
    monkeypatch.setattr(ocr, "ocr_available", lambda: True)
    seen = []

    def fake_ocr(pdf, page, **kwargs):
        seen.append(page)
        return "recognized"

    texts, count = ocr.ocr_pages(
        Path("x.pdf"), ["long " * 20, "", "short"], min_chars=20, ocr_page=fake_ocr
    )
    assert seen == [2, 3]
    assert texts[1] == "recognized"
    assert texts[2] == "recognized"
    assert count == 2


def test_ocr_pages_respects_max_pages(monkeypatch):
    monkeypatch.setattr(ocr, "ocr_available", lambda: True)
    texts, count = ocr.ocr_pages(
        Path("x.pdf"), ["", "", "", ""], max_pages=2, ocr_page=lambda *a, **k: "text"
    )
    assert count == 2
    assert texts[2] == ""  # beyond the limit


def test_ocr_pages_swallows_page_errors(monkeypatch):
    monkeypatch.setattr(ocr, "ocr_available", lambda: True)

    def boom(pdf, page, **kwargs):
        raise ocr.OcrError("nope")

    texts, count = ocr.ocr_pages(Path("x.pdf"), ["", "text"], ocr_page=boom)
    assert count == 0
    assert texts == ["", "text"]


def test_ocr_pages_without_tools_is_a_noop(monkeypatch):
    monkeypatch.setattr(ocr, "ocr_available", lambda: False)
    texts, count = ocr.ocr_pages(Path("x.pdf"), ["", ""])
    assert count == 0
    assert texts == ["", ""]
