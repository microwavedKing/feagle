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
    assert ocr.image_ocr_available() is True
    monkeypatch.setattr(ocr.shutil, "which", _which({"pdftoppm"}))
    assert ocr.ocr_available() is False
    assert ocr.image_ocr_available() is False
    # images need only tesseract, no renderer
    monkeypatch.setattr(ocr.shutil, "which", _which({"tesseract"}))
    assert ocr.ocr_available() is False
    assert ocr.image_ocr_available() is True


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
    assert calls[1] == [
        "tesseract", image, "stdout", "-l", "rus+eng",
        "--oem", "1", "-c", "preserve_interword_spaces=1",
    ]


def test_ocr_image_builds_expected_command(tmp_path, monkeypatch):
    image = tmp_path / "shot.png"
    image.write_bytes(b"PNG")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, b"hello screenshot\n", b"")

    monkeypatch.setattr(ocr.subprocess, "run", fake_run)

    text = ocr.ocr_image(image, langs="eng", tesseract="tesseract")
    assert text == "hello screenshot\n"
    assert calls == [[
        "tesseract", str(image), "stdout", "-l", "eng",
        "--oem", "1", "-c", "preserve_interword_spaces=1",
    ]]


def test_ocr_image_without_tesseract_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr.shutil, "which", _which(set()))
    with pytest.raises(ocr.OcrError, match="tesseract"):
        ocr.ocr_image(tmp_path / "x.png")


def test_ocr_image_reports_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(
        ocr.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, b"", b"boom"),
    )
    with pytest.raises(ocr.OcrError, match="tesseract exited"):
        ocr.ocr_image(tmp_path / "x.png", tesseract="tesseract")


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


# ---- page segmentation mode and OSD -------------------------------------


def test_resolve_psm_explicit_mode_wins_over_osd(monkeypatch):
    monkeypatch.setattr(ocr, "osd_available", lambda: True)
    assert ocr.resolve_psm(6, osd=True, langs="eng") == (6, "eng")


def test_resolve_psm_auto_leaves_the_tesseract_default(monkeypatch):
    monkeypatch.setattr(ocr, "osd_available", lambda: True)
    assert ocr.resolve_psm("auto", osd=False, langs="eng") == (None, "eng")
    assert ocr.resolve_psm(None, langs="eng") == (None, "eng")


def test_resolve_psm_with_osd_adds_mode_and_language(monkeypatch):
    monkeypatch.setattr(ocr, "osd_available", lambda: True)
    assert ocr.resolve_psm("auto", osd=True, langs="rus+eng") == (1, "rus+eng+osd")


def test_resolve_psm_does_not_duplicate_the_osd_language(monkeypatch):
    monkeypatch.setattr(ocr, "osd_available", lambda: True)
    assert ocr.resolve_psm("auto", osd=True, langs="eng+osd") == (1, "eng+osd")


def test_resolve_psm_without_osd_data_falls_back(monkeypatch):
    monkeypatch.setattr(ocr, "osd_available", lambda: False)
    assert ocr.resolve_psm("auto", osd=True, langs="eng") == (None, "eng")


def test_osd_available_is_checked_once(monkeypatch):
    calls = []

    def languages(**kwargs):
        calls.append(1)
        return {"osd", "eng"}

    monkeypatch.setattr(ocr, "tesseract_languages", languages)
    ocr.osd_available.cache_clear()
    try:
        assert ocr.osd_available() is True
        assert ocr.osd_available() is True
    finally:
        ocr.osd_available.cache_clear()
    assert calls == [1]


def test_ocr_image_passes_the_page_segmentation_mode(tmp_path, monkeypatch):
    image = tmp_path / "shot.png"
    image.write_bytes(b"PNG")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, b"text\n", b"")

    monkeypatch.setattr(ocr.subprocess, "run", fake_run)
    ocr.ocr_image(image, langs="eng", psm=7, tesseract="tesseract")
    assert calls[0][-2:] == ["--psm", "7"]


def test_ocr_image_without_a_mode_keeps_the_default_command(tmp_path, monkeypatch):
    image = tmp_path / "shot.png"
    image.write_bytes(b"PNG")
    calls = []
    monkeypatch.setattr(
        ocr.subprocess, "run",
        lambda cmd, **k: (calls.append(cmd), subprocess.CompletedProcess(cmd, 0, b"t", b""))[1],
    )
    ocr.ocr_image(image, langs="eng", tesseract="tesseract")
    assert "--psm" not in calls[0]


# ---- parallel page OCR ---------------------------------------------------


class InlineExecutor:
    """A ``ProcessPoolExecutor`` stand-in that runs each task immediately."""

    def __init__(self, workers):
        self.workers = workers
        self.submitted = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def submit(self, fn, payload):
        from concurrent.futures import Future

        self.submitted.append(payload)
        future = Future()
        try:
            future.set_result(fn(payload))
        except Exception as exc:  # noqa: BLE001 - mirror a real pool
            future.set_exception(exc)
        return future


def _fake_pages(monkeypatch, seen=None):
    def fake_page(pdf, page, **kwargs):
        if seen is not None:
            seen.append(page)
        return f"page {page} text"

    monkeypatch.setattr(ocr, "ocr_pdf_page", fake_page)
    monkeypatch.setattr(ocr, "ocr_available", lambda: True)


def test_ocr_pages_parallel_keeps_page_order(monkeypatch):
    seen = []
    _fake_pages(monkeypatch, seen)
    pools = []

    def factory(workers):
        pool = InlineExecutor(workers)
        pools.append(pool)
        return pool

    texts, count = ocr.ocr_pages(
        Path("x.pdf"), ["", "", ""], workers=2, executor_factory=factory
    )
    assert texts == ["page 1 text", "page 2 text", "page 3 text"]
    assert count == 3
    assert pools[0].workers == 2
    assert sorted(seen) == [1, 2, 3]


def test_ocr_pages_clamps_the_worker_count(monkeypatch):
    _fake_pages(monkeypatch)
    workers_seen = []
    ocr.ocr_pages(
        Path("x.pdf"), [""], workers=99,
        executor_factory=lambda workers: workers_seen.append(workers) or InlineExecutor(workers),
    )
    assert workers_seen == [ocr.MAX_OCR_WORKERS]


def test_ocr_pages_parallel_isolates_page_errors(monkeypatch):
    monkeypatch.setattr(ocr, "ocr_available", lambda: True)

    def flaky(pdf, page, **kwargs):
        if page == 2:
            raise ocr.OcrError("boom")
        return f"page {page}"

    monkeypatch.setattr(ocr, "ocr_pdf_page", flaky)
    texts, count = ocr.ocr_pages(
        Path("x.pdf"), ["", "", ""], workers=2, executor_factory=InlineExecutor
    )
    assert texts == ["page 1", "", "page 3"]
    assert count == 2


def test_ocr_pages_falls_back_when_the_pool_is_unavailable(monkeypatch):
    _fake_pages(monkeypatch)

    def no_pool(workers):
        raise RuntimeError("no processes here")

    texts, count = ocr.ocr_pages(
        Path("x.pdf"), ["", ""], workers=2, executor_factory=no_pool
    )
    assert texts == ["page 1 text", "page 2 text"]
    assert count == 2


def test_ocr_pages_with_one_worker_stays_in_process(monkeypatch):
    _fake_pages(monkeypatch)
    texts, count = ocr.ocr_pages(
        Path("x.pdf"), [""], workers=1,
        executor_factory=lambda workers: pytest.fail("must not start a pool"),
    )
    assert texts == ["page 1 text"]
    assert count == 1


def test_ocr_pages_custom_page_callable_disables_the_pool(monkeypatch):
    monkeypatch.setattr(ocr, "ocr_available", lambda: True)
    seen = []
    texts, count = ocr.ocr_pages(
        Path("x.pdf"), ["", ""], workers=2,
        ocr_page=lambda pdf, page, **kwargs: (seen.append(page), "text")[1],
        executor_factory=lambda workers: pytest.fail("must not pool a custom callable"),
    )
    assert count == 2
    assert seen == [1, 2]
