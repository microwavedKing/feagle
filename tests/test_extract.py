"""Tests for text extraction."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from feagle.indexer.extract import (
    ExtractionError,
    UnsupportedFile,
    extract,
    extract_pdf,
    pdftotext_available,
)

FIXTURES = Path(__file__).parent / "fixtures"
PDF_FIXTURE = FIXTURES / "two_pages.pdf"


def test_extracts_only_the_visible_text_of_html(tmp_path):
    path = tmp_path / "page.html"
    path.write_text(
        "<html><head><title>Hidden</title><style>b{color:red}</style></head>"
        "<body><h1>Feagle</h1><p>local &amp; offline search</p>"
        "<script>var x = 1;</script></body></html>",
        encoding="utf-8",
    )
    result = extract(path)
    assert result.kind == "text"
    text = result.pages[0]
    assert "Feagle" in text
    assert "local & offline search" in text  # entity decoded
    assert "<h1>" not in text and "<p>" not in text
    assert "var x" not in text  # script body dropped
    assert "Hidden" not in text  # <head> dropped


def test_extracts_xml_without_the_tags(tmp_path):
    path = tmp_path / "feed.xml"
    path.write_text("<rss><item><title>Hello world</title></item></rss>", encoding="utf-8")
    text = extract(path).pages[0]
    assert "Hello world" in text
    assert "<title>" not in text


def test_extracts_text_from_broken_markup(tmp_path):
    path = tmp_path / "broken.html"
    path.write_text("<html><body>some text <b>bold</body>", encoding="utf-8")
    assert "some text" in extract(path).pages[0]


def test_extracts_utf8_text(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("Привет, world!\nвторая строка", encoding="utf-8")
    result = extract(path)
    assert result.kind == "text"
    assert result.pages == ("Привет, world!\nвторая строка",)


def test_invalid_utf8_falls_back_without_replacement(tmp_path):
    path = tmp_path / "a.txt"
    path.write_bytes(b"ok \xff\xfe bytes")
    result = extract(path)
    assert "ok" in result.pages[0]
    assert "\ufffd" not in result.pages[0]  # cp1252 decodes every byte
    assert result.encoding == "cp1252"


def test_cp1251_russian_is_decoded(tmp_path):
    path = tmp_path / "ru.txt"
    path.write_bytes("Привет, мир! Это старая заметка.".encode("cp1251"))
    result = extract(path)
    assert result.encoding == "cp1251"
    assert "Привет, мир!" in result.pages[0]
    assert "заметка" in result.pages[0]
    assert "\ufffd" not in result.pages[0]


def test_cp1252_western_is_decoded(tmp_path):
    path = tmp_path / "fr.txt"
    path.write_bytes("Café crème, déjà vu".encode("cp1252"))
    result = extract(path)
    assert result.encoding == "cp1252"
    assert result.pages[0] == "Café crème, déjà vu"


def test_utf8_cyrillic_stays_utf8(tmp_path):
    path = tmp_path / "u.txt"
    path.write_text("Привет, мир!", encoding="utf-8")
    result = extract(path)
    assert result.encoding == "utf-8"
    assert result.pages[0] == "Привет, мир!"


def test_crlf_is_preserved(tmp_path):
    path = tmp_path / "a.md"
    path.write_bytes(b"line1\r\nline2\r\n")
    assert extract(path).pages[0] == "line1\r\nline2\r\n"


def test_binary_is_unsupported(tmp_path):
    path = tmp_path / "a.bin"
    path.write_bytes(b"MZ\x00\x00binary")
    with pytest.raises(UnsupportedFile):
        extract(path)


def test_nul_after_sniff_window_is_text(tmp_path):
    path = tmp_path / "a.txt"
    path.write_bytes(b"a" * 9000 + b"\x00tail")
    assert extract(path).kind == "text"


def test_pdf_pages_are_split(monkeypatch):
    if not pdftotext_available():
        pytest.skip("pdftotext not installed")
    result = extract_pdf(PDF_FIXTURE)
    assert result.kind == "pdf"
    assert len(result.pages) == 2
    assert "Page one heading" in result.pages[0]
    assert "first page" in result.pages[0]
    assert "Page two heading" in result.pages[1]
    assert "second page" in result.pages[1]


def test_extract_dispatches_pdf(monkeypatch):
    if not pdftotext_available():
        pytest.skip("pdftotext not installed")
    assert extract(PDF_FIXTURE).kind == "pdf"


# -- images (OCR) ----------------------------------------------------------


def test_extract_image_when_enabled(tmp_path, monkeypatch):
    path = tmp_path / "shot.png"
    path.write_bytes(b"PNG")
    monkeypatch.setattr("feagle.indexer.extract.image_ocr_available", lambda: True)
    monkeypatch.setattr("feagle.indexer.extract.ocr_image", lambda p, **kw: "recognized words")
    result = extract(path, ocr_images=True, ocr_langs="eng")
    assert result.kind == "image"
    assert result.pages == ("recognized words",)


def test_image_without_the_flag_is_unsupported(tmp_path, monkeypatch):
    path = tmp_path / "shot.png"
    path.write_bytes(b"PNG")
    monkeypatch.setattr(
        "feagle.indexer.extract.ocr_image",
        lambda *a, **k: pytest.fail("must not OCR when disabled"),
    )
    with pytest.raises(UnsupportedFile, match="ocr_images"):
        extract(path)


def test_image_without_tesseract_is_unsupported(tmp_path, monkeypatch):
    path = tmp_path / "shot.jpg"
    path.write_bytes(b"JPG")
    monkeypatch.setattr("feagle.indexer.extract.image_ocr_available", lambda: False)
    with pytest.raises(UnsupportedFile, match="tesseract"):
        extract(path, ocr_images=True)


def test_image_ocr_error_becomes_extraction_error(tmp_path, monkeypatch):
    from feagle.indexer.ocr import OcrError

    path = tmp_path / "shot.webp"
    path.write_bytes(b"WEBP")
    monkeypatch.setattr("feagle.indexer.extract.image_ocr_available", lambda: True)

    def boom(*args, **kwargs):
        raise OcrError("no text")

    monkeypatch.setattr("feagle.indexer.extract.ocr_image", boom)
    with pytest.raises(ExtractionError):
        extract(path, ocr_images=True)


def test_image_extensions_are_recognized(tmp_path, monkeypatch):
    from feagle.indexer.ocr import IMAGE_SUFFIXES

    monkeypatch.setattr("feagle.indexer.extract.image_ocr_available", lambda: True)
    monkeypatch.setattr("feagle.indexer.extract.ocr_image", lambda p, **kw: "text")
    for ext in IMAGE_SUFFIXES:
        path = tmp_path / f"shot{ext}"
        path.write_bytes(b"x")
        assert extract(path, ocr_images=True).kind == "image"


def test_image_media_only_has_empty_text(tmp_path, monkeypatch):
    path = tmp_path / "shot.png"
    path.write_bytes(b"PNG")
    monkeypatch.setattr(
        "feagle.indexer.extract.ocr_image",
        lambda *a, **k: pytest.fail("media-only images must not be OCR'd"),
    )
    result = extract(path, media_images=True)
    assert result.kind == "image"
    assert result.pages == ("",)


def test_pdf_without_poppler_is_unsupported(tmp_path, monkeypatch):
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF-1.7")
    monkeypatch.setattr("feagle.indexer.extract.shutil.which", lambda name: None)
    with pytest.raises(UnsupportedFile, match="poppler"):
        extract_pdf(path)


def test_invalid_pdf_is_an_error(tmp_path):
    if not pdftotext_available():
        pytest.skip("pdftotext not installed")
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.7 this is not a real pdf")
    with pytest.raises(ExtractionError):
        extract_pdf(path)


def test_pdf_timeout_is_reported(tmp_path, monkeypatch):
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF-1.7")
    # poppler is not installed everywhere; pretend it is and fake the timeout
    monkeypatch.setattr("feagle.indexer.extract.shutil.which", lambda name: "/usr/bin/pdftotext")

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="pdftotext", timeout=1)

    monkeypatch.setattr("feagle.indexer.extract.subprocess.run", fake_run)
    with pytest.raises(ExtractionError, match="timed out"):
        extract_pdf(path)


def test_pdf_ocr_is_invoked_for_empty_pages(tmp_path, monkeypatch):
    path = tmp_path / "scan.pdf"
    path.write_bytes(b"%PDF-1.7")
    monkeypatch.setattr("feagle.indexer.extract.shutil.which", lambda name: "/usr/bin/pdftotext")

    class Proc:
        returncode = 0
        stdout = b""
        stderr = b""

    monkeypatch.setattr("feagle.indexer.extract.subprocess.run", lambda *a, **k: Proc())
    captured = {}

    def fake_ocr(pdf, pages, **kwargs):
        captured["pages"] = list(pages)
        captured["kwargs"] = kwargs
        return (["recognized page"], 1)

    monkeypatch.setattr("feagle.indexer.extract.ocr_pages", fake_ocr)
    result = extract_pdf(path, ocr=True, ocr_langs="rus+eng", ocr_min_chars=5)
    assert result.pages == ("recognized page",)
    assert captured["pages"] == [""]
    assert captured["kwargs"]["langs"] == "rus+eng"


def _empty_pdf(tmp_path, monkeypatch):
    path = tmp_path / "scan.pdf"
    path.write_bytes(b"%PDF-1.7")
    monkeypatch.setattr("feagle.indexer.extract.shutil.which", lambda name: "/usr/bin/pdftotext")

    class Proc:
        returncode = 0
        stdout = b""
        stderr = b""

    monkeypatch.setattr("feagle.indexer.extract.subprocess.run", lambda *a, **k: Proc())
    return path


def test_pdf_ocr_passes_the_tuning_options(tmp_path, monkeypatch):
    path = _empty_pdf(tmp_path, monkeypatch)
    captured = {}

    def fake_ocr(pdf, pages, **kwargs):
        captured.update(kwargs)
        return pages, 0

    monkeypatch.setattr("feagle.indexer.extract.ocr_pages", fake_ocr)
    extract_pdf(path, ocr=True, ocr_psm=6, ocr_workers=2)
    assert captured["psm"] == 6
    assert captured["workers"] == 2


def test_pdf_ocr_with_osd_resolves_mode_and_language(tmp_path, monkeypatch):
    from feagle.indexer import ocr

    path = _empty_pdf(tmp_path, monkeypatch)
    monkeypatch.setattr(ocr, "osd_available", lambda: True)
    captured = {}

    def fake_ocr(pdf, pages, **kwargs):
        captured.update(kwargs)
        return pages, 0

    monkeypatch.setattr("feagle.indexer.extract.ocr_pages", fake_ocr)
    extract_pdf(path, ocr=True, ocr_langs="eng", ocr_osd=True)
    assert captured["langs"] == "eng+osd"
    assert captured["psm"] == 1


def test_image_ocr_resolves_the_mode(tmp_path, monkeypatch):
    path = tmp_path / "shot.png"
    path.write_bytes(b"PNG")
    monkeypatch.setattr("feagle.indexer.extract.image_ocr_available", lambda: True)
    captured = {}

    def fake_ocr(image, **kwargs):
        captured.update(kwargs)
        return "recognized"

    monkeypatch.setattr("feagle.indexer.extract.ocr_image", fake_ocr)
    result = extract(path, ocr_images=True, ocr_langs="eng", ocr_psm=11)
    assert result.pages == ("recognized",)
    assert captured["psm"] == 11


def test_pdf_without_ocr_does_not_call_ocr(tmp_path, monkeypatch):
    path = tmp_path / "scan.pdf"
    path.write_bytes(b"%PDF-1.7")
    monkeypatch.setattr("feagle.indexer.extract.shutil.which", lambda name: "/usr/bin/pdftotext")

    class Proc:
        returncode = 0
        stdout = b""
        stderr = b""

    monkeypatch.setattr("feagle.indexer.extract.subprocess.run", lambda *a, **k: Proc())

    def fail(*args, **kwargs):
        raise AssertionError("OCR must not run when disabled")

    monkeypatch.setattr("feagle.indexer.extract.ocr_pages", fail)
    assert extract_pdf(path).pages == ("",)


def test_empty_file(tmp_path):
    path = tmp_path / "empty.txt"
    path.write_text("", encoding="utf-8")
    assert extract(path).pages == ("",)


def test_max_bytes_is_enforced(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("x" * 100, encoding="utf-8")
    with pytest.raises(ExtractionError):
        extract(path, max_bytes=10)


def test_missing_file_raises(tmp_path):
    with pytest.raises(ExtractionError):
        extract(tmp_path / "nope.txt")
