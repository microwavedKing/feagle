"""Tests for the incremental indexing pipeline."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from feagle import config, db
from feagle.indexer import asr, extract, pipeline
from feagle.indexer.extract import ExtractionError
from feagle.search.fts import search_fts

PDF_FIXTURE = Path(__file__).parent / "fixtures" / "two_pages.pdf"


@pytest.fixture
def conn(tmp_path):
    connection = db.open_database(tmp_path / "index.sqlite")
    yield connection
    connection.close()


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "root"
    path.mkdir()
    return path


def make_cfg(root, **index_overrides):
    cfg = config.default_config()
    cfg["index"]["roots"] = [str(root)]
    cfg["index"].update(index_overrides)
    return cfg


def fts_hits(conn, term):
    rows = conn.execute(
        "SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH ?", (f'"{term}"',)
    ).fetchall()
    return [r["chunk_id"] for r in rows]


def file_row(conn, path):
    return conn.execute("SELECT * FROM files WHERE path = ?", (os.path.realpath(path),)).fetchone()


def test_initial_index(root, conn):
    (root / "a.txt").write_text("hello world", encoding="utf-8")
    (root / "b.md").write_text("second document", encoding="utf-8")

    stats = pipeline.index_roots(conn, make_cfg(root))
    assert stats.reindexed == 2
    assert stats.errors == 0
    assert stats.chunks == 2
    assert conn.execute("SELECT COUNT(*) AS n FROM files").fetchone()["n"] == 2


def test_second_run_is_unchanged(root, conn):
    (root / "a.txt").write_text("hello world", encoding="utf-8")
    pipeline.index_roots(conn, make_cfg(root))
    stats = pipeline.index_roots(conn, make_cfg(root))
    assert stats.unchanged == 1
    assert stats.reindexed == 0
    assert stats.chunks == 0


def test_mtime_only_change_touches(root, conn):
    path = root / "a.txt"
    path.write_text("stable content", encoding="utf-8")
    pipeline.index_roots(conn, make_cfg(root))
    before = file_row(conn, path)
    before_chunks = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]

    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000_000))

    stats = pipeline.index_roots(conn, make_cfg(root))
    assert stats.touched == 1
    assert stats.reindexed == 0
    after = file_row(conn, path)
    assert after["id"] == before["id"]
    assert after["mtime_ns"] != before["mtime_ns"]
    assert conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"] == before_chunks


def test_content_change_replaces_old_chunks_and_fts(root, conn):
    path = root / "a.txt"
    path.write_text("alpha content", encoding="utf-8")
    pipeline.index_roots(conn, make_cfg(root))
    assert fts_hits(conn, "alpha")

    path.write_text("beta content", encoding="utf-8")
    stats = pipeline.index_roots(conn, make_cfg(root))
    assert stats.reindexed == 1
    assert fts_hits(conn, "alpha") == []
    assert fts_hits(conn, "beta")


def test_binary_file_is_unsupported(root, conn):
    (root / "blob.txt").write_bytes(b"MZ\x00\x00binary")
    stats = pipeline.index_roots(conn, make_cfg(root))
    assert stats.unsupported == 1
    assert file_row(conn, root / "blob.txt")["state"] == "unsupported"


def test_pdf_pages_are_indexed(root, conn):
    if not extract.pdftotext_available():
        pytest.skip("pdftotext not installed")
    shutil.copy(PDF_FIXTURE, root / "doc.pdf")

    stats = pipeline.index_roots(conn, make_cfg(root))
    assert stats.reindexed == 1
    pages = [row["page"] for row in conn.execute("SELECT page FROM chunks ORDER BY seq")]
    assert pages == [1, 2]  # one chunk per page, page numbers preserved

    hits = search_fts(conn, "heading")
    assert {hit.page for hit in hits} == {1, 2}


def test_invalid_pdf_is_recorded_as_error(root, conn):
    if not extract.pdftotext_available():
        pytest.skip("pdftotext not installed")
    (root / "bad.pdf").write_bytes(b"%PDF-1.7 not really a pdf")
    stats = pipeline.index_roots(conn, make_cfg(root))
    assert stats.errors == 1
    assert file_row(conn, root / "bad.pdf")["state"] == "error"


def test_extraction_error_is_recorded(root, conn, monkeypatch):
    path = root / "a.txt"
    path.write_text("content", encoding="utf-8")

    def boom(*args, **kwargs):
        raise ExtractionError("boom")

    monkeypatch.setattr(pipeline, "extract", boom)
    stats = pipeline.index_roots(conn, make_cfg(root))
    assert stats.errors == 1
    assert file_row(conn, path)["state"] == "error"


def test_max_chunks_per_file(root, conn):
    body = "\n".join(f"line {i} " + "x" * 60 for i in range(300))
    (root / "big.txt").write_text(body, encoding="utf-8")
    stats = pipeline.index_roots(conn, make_cfg(root, max_chunks_per_file=3))
    assert stats.chunks == 3
    assert conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"] == 3


def test_full_forces_reindex(root, conn):
    (root / "a.txt").write_text("content", encoding="utf-8")
    pipeline.index_roots(conn, make_cfg(root))
    stats = pipeline.index_roots(conn, make_cfg(root), full=True)
    assert stats.reindexed == 1
    assert stats.unchanged == 0


def test_rebuild_wipes_and_rescans(root, conn):
    (root / "a.txt").write_text("content", encoding="utf-8")
    (root / "b.txt").write_text("more content", encoding="utf-8")
    pipeline.index_roots(conn, make_cfg(root))
    stats = pipeline.index_roots(conn, make_cfg(root), rebuild=True)
    assert stats.reindexed == 2
    assert conn.execute("SELECT COUNT(*) AS n FROM files").fetchone()["n"] == 2


def test_removed_file_is_pruned(root, conn):
    path = root / "a.txt"
    path.write_text("content", encoding="utf-8")
    (root / "b.txt").write_text("keep me", encoding="utf-8")
    pipeline.index_roots(conn, make_cfg(root))

    path.unlink()
    stats = pipeline.index_roots(conn, make_cfg(root))
    assert stats.pruned == 1
    assert file_row(conn, path) is None
    assert conn.execute("SELECT COUNT(*) AS n FROM files").fetchone()["n"] == 1


def test_a_root_removed_from_the_config_is_pruned(root, conn):
    other = root.parent / "other"
    other.mkdir()
    (other / "secret.txt").write_text("secret", encoding="utf-8")
    (root / "a.txt").write_text("inside", encoding="utf-8")

    two_roots = make_cfg(root)
    two_roots["index"]["roots"] = [str(root), str(other)]
    pipeline.index_roots(conn, two_roots)
    assert file_row(conn, other / "secret.txt") is not None

    # Dropping ``other`` from the config drops its files from the index; a pass
    # over the configured roots is authoritative (the privacy fix).
    stats = pipeline.index_roots(conn, make_cfg(root))
    assert stats.pruned == 1
    assert file_row(conn, other / "secret.txt") is None
    assert file_row(conn, root / "a.txt") is not None
    assert stats.scanned == 1


def test_explicit_roots_only_touch_what_they_scanned(root, conn):
    other = root.parent / "other"
    other.mkdir()
    (other / "keep.txt").write_text("outside", encoding="utf-8")
    (root / "a.txt").write_text("inside", encoding="utf-8")

    cfg = make_cfg(root)
    cfg["index"]["roots"] = [str(root), str(other)]
    pipeline.index_roots(conn, cfg)

    # ``feagle index --root PATH`` (explicit roots) must not drop other roots.
    stats = pipeline.index_roots(conn, cfg, roots=[str(root)])
    assert stats.pruned == 0
    assert file_row(conn, other / "keep.txt") is not None


def test_a_temporarily_missing_root_keeps_its_files(root, conn, tmp_path):
    (root / "a.txt").write_text("inside", encoding="utf-8")
    cfg = make_cfg(root)
    pipeline.index_roots(conn, cfg)

    away = tmp_path / "root-away"
    root.rename(away)  # e.g. an external drive that got unmounted

    stats = pipeline.index_roots(conn, cfg)
    assert stats.scanned == 0
    assert stats.pruned == 0
    # the row (with its original path) is kept, so a remount costs nothing
    assert file_row(conn, root / "a.txt") is not None


def test_removed_file_is_pruned_when_full(root, conn):
    path = root / "a.txt"
    path.write_text("content", encoding="utf-8")
    (root / "b.txt").write_text("keep me", encoding="utf-8")
    pipeline.index_roots(conn, make_cfg(root))

    path.unlink()
    stats = pipeline.index_roots(conn, make_cfg(root), full=True)
    assert stats.pruned == 1
    assert file_row(conn, root / "b.txt") is not None


def test_missing_root_is_skipped(root, conn):
    stats = pipeline.index_roots(conn, make_cfg(root / "nope"))
    assert stats.scanned == 0


# -- images (OCR) ----------------------------------------------------------


def test_scanner_allowlist_gains_images_with_ocr_images(root):
    assert ".png" not in pipeline._build_scanner(make_cfg(root)).include_ext
    scanner = pipeline._build_scanner(make_cfg(root, ocr_images=True))
    assert {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".tif"} <= scanner.include_ext


def test_images_are_ignored_when_ocr_images_is_off(root, conn):
    (root / "shot.png").write_bytes(b"PNG")
    stats = pipeline.index_roots(conn, make_cfg(root))
    assert stats.scanned == 0
    assert file_row(conn, root / "shot.png") is None


def test_images_are_indexed_and_searchable(root, conn, monkeypatch):
    (root / "shot.png").write_bytes(b"PNG")
    monkeypatch.setattr(extract, "image_ocr_available", lambda: True)
    monkeypatch.setattr(extract, "ocr_image", lambda p, **kw: "charlie screenshot words")

    stats = pipeline.index_roots(conn, make_cfg(root, ocr_images=True))
    assert stats.reindexed == 1
    hits = search_fts(conn, "charlie")
    assert hits and hits[0].path.endswith("shot.png")
    # an image has no meaningful line or page
    row = conn.execute("SELECT line, page FROM chunks").fetchone()
    assert row["line"] is None and row["page"] is None


# -- media image embeddings ------------------------------------------------


class FakeImageEmbedder:
    dim = 4

    def __init__(self):
        self.seen: list = []

    def embed_documents(self, items):
        return [[1.0] * self.dim for _ in items]

    def embed_queries(self, texts):
        return [[0.0] * self.dim for _ in texts]

    def embed_images(self, paths):
        self.seen.extend(paths)
        return [[2.0] * self.dim for _ in paths]


def enable_media(cfg, **overrides):
    cfg["media"].update({"images": True, "image_max_dim": 1024, "images_per_run": 0})
    cfg["media"].update(overrides)
    return cfg


def test_scanner_allowlist_gains_images_with_media_images(root):
    cfg = make_cfg(root)
    cfg["media"]["images"] = True
    assert ".png" in pipeline._build_scanner(cfg).include_ext


def test_media_images_are_indexed_as_one_vector(root, conn, monkeypatch):
    (root / "shot.png").write_bytes(b"PNG")
    monkeypatch.setattr(pipeline, "downscale_image", lambda src, dest, *, max_dim: src)
    embedder = FakeImageEmbedder()

    stats = pipeline.index_roots(conn, enable_media(make_cfg(root)), embedder=embedder)

    assert stats.reindexed == 1
    assert [p.name for p in embedder.seen] == ["shot.png"]
    assert conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM chunks_vec").fetchone()["n"] == 1
    assert conn.execute("SELECT body FROM chunks").fetchone()["body"] == ""  # no OCR


def test_media_images_respect_the_per_run_budget(root, conn, monkeypatch):
    for i in range(3):
        (root / f"{i}.png").write_bytes(b"PNG")
    monkeypatch.setattr(pipeline, "downscale_image", lambda src, dest, *, max_dim: src)
    embedder = FakeImageEmbedder()

    stats = pipeline.index_roots(
        conn, enable_media(make_cfg(root), images_per_run=1), embedder=embedder
    )

    assert stats.reindexed == 3
    assert len(embedder.seen) == 1  # only the first image fit the budget
    assert conn.execute("SELECT COUNT(*) AS n FROM files WHERE embedded=1").fetchone()["n"] == 1


def test_media_image_uses_ocr_text_as_body(root, conn, monkeypatch):
    (root / "shot.png").write_bytes(b"PNG")
    monkeypatch.setattr(extract, "image_ocr_available", lambda: True)
    monkeypatch.setattr(extract, "ocr_image", lambda p, **kw: "charlie text")
    monkeypatch.setattr(pipeline, "downscale_image", lambda src, dest, *, max_dim: src)

    cfg = enable_media(make_cfg(root, ocr_images=True))
    pipeline.index_roots(conn, cfg, embedder=FakeImageEmbedder())

    assert conn.execute("SELECT body FROM chunks").fetchone()["body"] == "charlie text"


def test_media_image_degrades_without_ffmpeg(root, conn, monkeypatch):
    from feagle.indexer.media import MediaError

    (root / "shot.png").write_bytes(b"PNG")

    def boom(*args, **kwargs):
        raise MediaError("ffmpeg not found")

    monkeypatch.setattr(pipeline, "downscale_image", boom)
    stats = pipeline.index_roots(conn, enable_media(make_cfg(root)), embedder=FakeImageEmbedder())

    assert stats.errors == 0
    assert conn.execute("SELECT embedded FROM files").fetchone()["embedded"] == 0


def test_backfill_embeds_media_images(root, conn, monkeypatch):
    (root / "shot.png").write_bytes(b"PNG")
    monkeypatch.setattr(pipeline, "downscale_image", lambda src, dest, *, max_dim: src)
    cfg = enable_media(make_cfg(root))
    pipeline.index_roots(conn, cfg)  # no embedder -> embedded=0

    assert pipeline.backfill(conn, cfg, FakeImageEmbedder()) == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM chunks_vec").fetchone()["n"] == 1


# -- video -----------------------------------------------------------------


def test_video_is_indexed_as_frames_with_timestamps(root, conn, monkeypatch):
    (root / "clip.mp4").write_bytes(b"MP4")
    monkeypatch.setattr(pipeline, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(pipeline, "ffprobe_available", lambda: True)

    def fake_frames(path, dest, *, interval, max_frames, max_dim):
        dest = Path(dest)
        frames = []
        for i in range(3):
            frame = dest / f"frame_{i:05d}.png"
            frame.write_bytes(b"PNG")
            frames.append((i * 10000, frame))
        return frames

    monkeypatch.setattr(pipeline, "extract_frames", fake_frames)
    embedder = FakeImageEmbedder()

    stats = pipeline.index_roots(
        conn, enable_media(make_cfg(root), video=True), embedder=embedder
    )

    assert stats.reindexed == 1
    times = [row["time_ms"] for row in conn.execute("SELECT time_ms FROM chunks ORDER BY seq")]
    assert times == [0, 10000, 20000]
    assert conn.execute("SELECT COUNT(*) AS n FROM chunks_vec").fetchone()["n"] == 3
    assert len(embedder.seen) == 3


def test_video_without_ffmpeg_is_unsupported(root, conn, monkeypatch):
    (root / "clip.mp4").write_bytes(b"MP4")
    monkeypatch.setattr(pipeline, "ffmpeg_available", lambda: False)
    stats = pipeline.index_roots(conn, enable_media(make_cfg(root), video=True))

    assert stats.unsupported == 1
    assert conn.execute("SELECT state FROM files").fetchone()["state"] == "unsupported"


# -- video OCR -------------------------------------------------------------


def make_frame_extractor(count=2, step_ms=1000):
    def fake_frames(path, dest, *, interval, max_frames, max_dim):
        dest = Path(dest)
        frames = []
        for i in range(count):
            frame = dest / f"frame_{i:05d}.png"
            frame.write_bytes(b"PNG")
            frames.append((i * step_ms, frame))
        return frames

    return fake_frames


def _enable_video_ocr(cfg, **overrides):
    return enable_media(cfg, video=True, video_ocr=True, **overrides)


def test_video_frames_are_ocrd_when_enabled(root, conn, monkeypatch):
    (root / "clip.mp4").write_bytes(b"MP4")
    monkeypatch.setattr(pipeline, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(pipeline, "ffprobe_available", lambda: True)
    monkeypatch.setattr(pipeline, "extract_frames", make_frame_extractor())
    monkeypatch.setattr(pipeline, "image_ocr_available", lambda: True)
    monkeypatch.setattr(pipeline, "ocr_image", lambda frame, **kw: "SIGN " + frame.stem)

    pipeline.index_roots(conn, _enable_video_ocr(make_cfg(root)), embedder=FakeImageEmbedder())

    rows = conn.execute("SELECT body FROM chunks ORDER BY seq").fetchall()
    assert [r["body"] for r in rows] == ["SIGN frame_00000", "SIGN frame_00001"]
    assert len(search_fts(conn, "sign")) == 2  # text shown in the video is searchable


def test_video_frames_have_no_text_without_ocr(root, conn, monkeypatch):
    (root / "clip.mp4").write_bytes(b"MP4")
    monkeypatch.setattr(pipeline, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(pipeline, "ffprobe_available", lambda: True)
    monkeypatch.setattr(pipeline, "extract_frames", make_frame_extractor())

    pipeline.index_roots(
        conn, enable_media(make_cfg(root), video=True), embedder=FakeImageEmbedder()
    )

    assert all(r["body"] == "" for r in conn.execute("SELECT body FROM chunks"))


def test_video_ocr_missing_tesseract_degrades(root, conn, monkeypatch):
    (root / "clip.mp4").write_bytes(b"MP4")
    monkeypatch.setattr(pipeline, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(pipeline, "ffprobe_available", lambda: True)
    monkeypatch.setattr(pipeline, "extract_frames", make_frame_extractor())
    monkeypatch.setattr(pipeline, "image_ocr_available", lambda: False)
    monkeypatch.setattr(
        pipeline, "ocr_image", lambda *a, **k: pytest.fail("must not OCR without tesseract")
    )

    stats = pipeline.index_roots(
        conn, _enable_video_ocr(make_cfg(root)), embedder=FakeImageEmbedder()
    )

    assert stats.errors == 0
    assert all(r["body"] == "" for r in conn.execute("SELECT body FROM chunks"))
    assert conn.execute("SELECT COUNT(*) AS n FROM chunks_vec").fetchone()["n"] == 2


# -- audio (ASR) -----------------------------------------------------------


def test_audio_is_transcribed_into_timed_chunks(root, conn, monkeypatch):
    (root / "talk.mp3").write_bytes(b"MP3")
    monkeypatch.setattr(asr, "asr_available", lambda binary=None: True)
    monkeypatch.setattr(
        asr, "transcribe",
        lambda path, **kwargs: [
            asr.Segment(0, 2000, "Hello there"),
            asr.Segment(2000, 4000, "General Kenobi"),
        ],
    )

    stats = pipeline.index_roots(
        conn, enable_media(make_cfg(root), audio=True), embedder=FakeImageEmbedder()
    )

    assert stats.reindexed == 1
    rows = conn.execute("SELECT time_ms, body FROM chunks ORDER BY seq").fetchall()
    assert [(r["time_ms"], r["body"]) for r in rows] == [
        (0, "Hello there"),
        (2000, "General Kenobi"),
    ]
    assert conn.execute("SELECT COUNT(*) AS n FROM chunks_vec").fetchone()["n"] == 2
    hits = search_fts(conn, "kenobi")
    assert hits and hits[0].path.endswith("talk.mp3")
    assert hits[0].time_ms == 2000


def test_audio_without_whisper_is_unsupported(root, conn, monkeypatch):
    (root / "talk.mp3").write_bytes(b"MP3")
    monkeypatch.setattr(asr, "asr_available", lambda binary=None: False)
    stats = pipeline.index_roots(conn, enable_media(make_cfg(root), audio=True))

    assert stats.unsupported == 1
    assert conn.execute("SELECT state FROM files").fetchone()["state"] == "unsupported"


def test_pause_check_stops_the_run(root, conn):
    for i in range(5):
        (root / f"f{i}.txt").write_text(f"content {i}", encoding="utf-8")

    checked = {"n": 0}

    def pause_check():
        checked["n"] += 1
        return "on battery" if checked["n"] > 2 else None

    stats = pipeline.index_roots(conn, make_cfg(root), pause_check=pause_check)
    assert stats.paused == "on battery"
    assert stats.scanned == 2
    assert "paused=" in stats.summary()
    # A paused pass must not reconcile: it has not seen the rest yet.
    assert stats.pruned == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM files").fetchone()["n"] == 2

    # a later run without the pause finishes the remaining files
    rest = pipeline.index_roots(conn, make_cfg(root))
    assert rest.reindexed == 3


# ---- targeted indexing (watch events) -----------------------------------


def test_index_paths_indexes_only_the_given_file(root, conn):
    (root / "a.txt").write_text("hello world", encoding="utf-8")
    (root / "b.txt").write_text("second document", encoding="utf-8")
    pipeline.index_roots(conn, make_cfg(root))

    (root / "a.txt").write_text("hello changed world", encoding="utf-8")
    stats = pipeline.index_paths(conn, make_cfg(root), [root / "a.txt"])
    assert stats.scanned == 1
    assert stats.reindexed == 1
    assert stats.unchanged == 0
    assert fts_hits(conn, "changed")
    assert file_row(conn, root / "b.txt") is not None


def test_index_paths_does_not_walk_the_root(root, conn, monkeypatch):
    (root / "a.txt").write_text("hello world", encoding="utf-8")
    walks = []

    def no_walk(self, roots):
        walks.append(list(roots))
        return iter(())

    monkeypatch.setattr(pipeline.Scanner, "iter_files", no_walk)
    pipeline.index_paths(conn, make_cfg(root), [root / "a.txt"])
    assert walks == []  # a file event never walks the tree
    assert file_row(conn, root / "a.txt") is not None


def test_index_paths_prunes_a_deleted_file(root, conn):
    target = root / "a.txt"
    target.write_text("hello world", encoding="utf-8")
    pipeline.index_roots(conn, make_cfg(root))

    target.unlink()
    stats = pipeline.index_paths(conn, make_cfg(root), [target])
    assert stats.pruned == 1
    assert file_row(conn, target) is None


def test_index_paths_keeps_a_filtered_but_existing_file(root, conn):
    target = root / "a.txt"
    target.write_text("hello world", encoding="utf-8")
    pipeline.index_roots(conn, make_cfg(root))

    # .txt is no longer allowlisted, but the file is still there: do not prune.
    stats = pipeline.index_paths(conn, make_cfg(root, include_ext=[".md"]), [target])
    assert stats.pruned == 0
    assert stats.scanned == 0
    assert file_row(conn, target) is not None


def test_index_paths_walks_a_changed_directory(root, conn):
    sub = root / "sub"
    sub.mkdir()
    (sub / "a.txt").write_text("one", encoding="utf-8")
    (sub / "b.txt").write_text("two", encoding="utf-8")

    stats = pipeline.index_paths(conn, make_cfg(root), [sub])
    assert stats.reindexed == 2


def test_index_paths_honours_the_pause(root, conn):
    (root / "a.txt").write_text("hello world", encoding="utf-8")
    stats = pipeline.index_paths(
        conn, make_cfg(root), [root / "a.txt"], pause_check=lambda: "on battery"
    )
    assert stats.paused == "on battery"
    assert stats.scanned == 0
