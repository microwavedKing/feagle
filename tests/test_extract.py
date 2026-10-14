"""Tests for text extraction."""

from __future__ import annotations

import pytest

from feagle.indexer.extract import ExtractionError, UnsupportedFile, extract


def test_extracts_utf8_text(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("Привет, world!\nвторая строка", encoding="utf-8")
    result = extract(path)
    assert result.kind == "text"
    assert result.pages == ("Привет, world!\nвторая строка",)


def test_invalid_utf8_is_replaced(tmp_path):
    path = tmp_path / "a.txt"
    path.write_bytes(b"ok \xff\xfe bytes")
    result = extract(path)
    assert "ok" in result.pages[0]
    assert "\ufffd" in result.pages[0]


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


def test_pdf_is_not_implemented_yet(tmp_path):
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF-1.7 fake")
    with pytest.raises(UnsupportedFile, match="not implemented"):
        extract(path)


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
