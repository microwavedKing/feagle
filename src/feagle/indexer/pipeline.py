"""Incremental indexing pipeline.

For every scanned file we compare ``(size, mtime_ns)`` with the database and, if
they differ, hash the content (blake2b). An unchanged hash only refreshes the
stored stat ("touch"); a changed hash triggers extraction and re-chunking in a
single transaction per file. When an :class:`~feagle.embeddings.service.Embedder`
is supplied, chunk vectors are computed *before* the write transaction (so the
write lock is never held during a network call) and stored in ``chunks_vec``.

The indexer is the only writer. If embeddings fail, chunks are still stored so
that full-text search keeps working; such files stay ``embedded=0`` and are
completed later by :func:`backfill`.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import db, paths
from ..embeddings.client import EmbeddingError, ServerUnavailable
from ..log import get_logger
from . import asr
from .chunker import MAX_CHARS, Chunk, chunk_extracted
from .extract import ExtractionError, UnsupportedFile, extract
from .media import (
    VIDEO_SUFFIXES,
    MediaError,
    downscale_image,
    extract_frames,
    ffmpeg_available,
    ffprobe_available,
)
from .ocr import IMAGE_SUFFIXES, OcrError, image_ocr_available, ocr_image
from .scanner import ScanCandidate, Scanner

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable, Iterable, Sequence

    from ..embeddings.service import Embedder

log = get_logger("indexer.pipeline")

_HASH_CHUNK = 1 << 20  # 1 MiB


@dataclass
class IndexStats:
    """Aggregate result of one indexing run."""

    scanned: int = 0
    unchanged: int = 0
    touched: int = 0
    reindexed: int = 0
    unsupported: int = 0
    errors: int = 0
    pruned: int = 0
    chunks: int = 0
    vectors: int = 0
    backfilled: int = 0
    paused: str | None = None
    error_samples: list[str] = field(default_factory=list)

    def summary(self) -> str:
        base = (
            f"scanned={self.scanned} reindexed={self.reindexed} "
            f"touched={self.touched} unchanged={self.unchanged} "
            f"unsupported={self.unsupported} errors={self.errors} "
            f"pruned={self.pruned} chunks={self.chunks} vectors={self.vectors} "
            f"backfilled={self.backfilled}"
        )
        if self.paused:
            base += f" paused={self.paused!r}"
        return base


def hash_file(path: Path, *, chunk_size: int = _HASH_CHUNK) -> str:
    """Stream ``path`` through blake2b and return the hex digest."""
    digest = hashlib.blake2b()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _canonical(path: Path) -> str:
    return os.path.realpath(path)


def _now() -> int:
    return int(time.time())


def _delete_file_chunks(conn: sqlite3.Connection, file_id: int) -> list[int]:
    """Delete a file's chunks (and their FTS rows); return the deleted chunk ids."""
    ids = [row["id"] for row in conn.execute("SELECT id FROM chunks WHERE file_id = ?", (file_id,))]
    conn.execute(
        "DELETE FROM chunks_fts WHERE chunk_id IN (SELECT id FROM chunks WHERE file_id = ?)",
        (file_id,),
    )
    conn.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))
    return ids


def _upsert_file(
    conn: sqlite3.Connection,
    path: str,
    candidate: ScanCandidate,
    digest: str | None,
    *,
    state: str,
    error: str | None,
) -> int:
    row = conn.execute("SELECT id FROM files WHERE path = ?", (path,)).fetchone()
    if row is not None:
        file_id = row["id"]
        _delete_file_chunks(conn, file_id)
        conn.execute(
            "UPDATE files SET size=?, mtime_ns=?, hash=?, state=?, error=?, "
            "embedded=0, updated_at=? WHERE id=?",
            (candidate.size, candidate.mtime_ns, digest, state, error, _now(), file_id),
        )
    else:
        cur = conn.execute(
            "INSERT INTO files(path, size, mtime_ns, hash, state, error, embedded, updated_at) "
            "VALUES(?,?,?,?,?,?,0,?)",
            (path, candidate.size, candidate.mtime_ns, digest, state, error, _now()),
        )
        file_id = int(cur.lastrowid or 0)
    return file_id


def _write_chunks(conn: sqlite3.Connection, file_id: int, chunks: list[Any]) -> list[int]:
    from ..stemming import stem_text

    ids: list[int] = []
    for chunk in chunks:
        cur = conn.execute(
            "INSERT INTO chunks(file_id, seq, page, line, time_ms, body) VALUES(?,?,?,?,?,?)",
            (file_id, chunk.seq, chunk.page, chunk.line, chunk.time_ms, chunk.body),
        )
        chunk_id = int(cur.lastrowid or 0)
        conn.execute(
            "INSERT INTO chunks_fts(body, stem, chunk_id) VALUES(?,?,?)",
            (chunk.body, stem_text(chunk.body), chunk_id),
        )
        ids.append(chunk_id)
    return ids


def _embed(
    embedder: Embedder, items: list[tuple[str, str | None]]
) -> Sequence[Sequence[float] | None] | None:
    """Embed documents, degrading gracefully instead of failing the file.

    Returns a list aligned with ``items`` (``None`` marks a chunk that could not
    be embedded), or ``None`` when nothing can be embedded **right now** because
    the server is unavailable. The two are different outcomes for a caller that
    walks many files: an all-``None`` list is about this file's content (an input
    the model rejects), while ``None`` means the embeddings server is down and
    the walk should stop rather than hammer it. A batch that the server rejects
    (for example an input larger than the slot context) is retried chunk by chunk
    so that one bad chunk does not cost the whole file its vectors.
    """
    try:
        return embedder.embed_documents(items)
    except ServerUnavailable as exc:
        log.warning("embeddings server unavailable: %s", exc)
        return None
    except EmbeddingError as exc:
        log.warning("batch embedding failed, retrying chunk by chunk: %s", exc)

    vectors: list[list[float] | None] = []
    for item in items:
        try:
            vectors.append(embedder.embed_documents([item])[0])
        except ServerUnavailable as exc:
            log.warning("embeddings server unavailable: %s", exc)
            return None
        except EmbeddingError as exc:
            log.warning("chunk cannot be embedded, skipping it: %s", exc)
            vectors.append(None)

    return vectors


def _image_chunk(text: str) -> Chunk:
    """A single chunk for an image: its embedding is one vector per file."""
    body = " ".join(text.split())[:MAX_CHARS]
    return Chunk(seq=0, page=None, line=None, body=body)


def _embed_prepared(embedder: Embedder, paths: list[Path]) -> list[list[float]] | None:
    """Embed already-prepared images (no downscaling here). ``None`` on failure."""
    embed_images = getattr(embedder, "embed_images", None)
    if embed_images is None:
        log.warning("embedder cannot embed images; skipping %d file(s)", len(paths))
        return None
    try:
        return embed_images(paths)
    except ServerUnavailable as exc:
        log.warning("embeddings server unavailable: %s", exc)
        return None
    except EmbeddingError as exc:
        log.warning("image embedding failed: %s", exc)
        return None


def _embed_image(
    embedder: Embedder, path: Path, media_cfg: dict[str, Any]
) -> list[list[float]] | None:
    """Embed one image file (downscaled first). Returns ``[vector]`` or ``None``."""
    try:
        with tempfile.TemporaryDirectory(prefix="feagle-media-") as tmp:
            prepared = downscale_image(
                path, Path(tmp) / "image.png", max_dim=int(media_cfg["image_max_dim"])
            )
            return _embed_prepared(embedder, [prepared])
    except MediaError as exc:
        log.warning("cannot prepare image %s: %s", path, exc)
        return None


def _ocr_frames(
    frames: list[tuple[int, Path]], *, ocr: bool, ocr_langs: str
) -> list[str]:
    """OCR each frame to text (empty strings when OCR is off or unavailable)."""
    if not ocr:
        return [""] * len(frames)
    if not image_ocr_available():
        log.warning("video OCR requested but tesseract is missing; frames have no text")
        return [""] * len(frames)
    texts: list[str] = []
    for _time_ms, frame in frames:
        try:
            text = ocr_image(frame, langs=ocr_langs)
        except OcrError as exc:
            log.warning("OCR failed for frame %s: %s", frame, exc)
            text = ""
        texts.append(" ".join(text.split()))
    return texts


def _index_video_frames(
    path: Path,
    media_cfg: dict[str, Any],
    embedder: Embedder | None,
    *,
    allow_image: bool,
    ocr: bool = False,
    ocr_langs: str = "rus+eng",
) -> tuple[list[Chunk], Sequence[Sequence[float] | None] | None]:
    """Extract frames and build one chunk per frame (with its timestamp).

    With ``ocr`` each frame's text becomes the chunk body (so it is searchable
    with full text), while the frame vector stays an image embedding.
    """
    with tempfile.TemporaryDirectory(prefix="feagle-video-") as tmp:
        frames = extract_frames(
            path,
            Path(tmp),
            interval=float(media_cfg["video_frame_interval"]),
            max_frames=int(media_cfg["video_max_frames"]),
            max_dim=int(media_cfg["image_max_dim"]),
        )
        bodies = _ocr_frames(frames, ocr=ocr, ocr_langs=ocr_langs)
        chunks = [
            Chunk(seq=index, page=None, line=None, body=bodies[index], time_ms=time_ms)
            for index, (time_ms, _frame) in enumerate(frames)
        ]
        vectors: Sequence[Sequence[float] | None] | None = None
        if embedder is not None and chunks and allow_image:
            vectors = _embed_prepared(embedder, [frame for _time, frame in frames])
        return chunks, vectors


def _audio_chunks(path: Path, media_cfg: dict[str, Any], max_chunks: int) -> list[Chunk]:
    """Transcribe ``path`` and build one chunk per spoken segment."""
    segments = asr.transcribe(
        path,
        model=asr.resolve_model(media_cfg["whisper_model"]),
        whisper=media_cfg.get("whisper_binary"),
        language=str(media_cfg.get("audio_language", "auto")),
    )
    return [
        Chunk(seq=index, page=None, line=None, body=segment.text, time_ms=segment.start_ms)
        for index, segment in enumerate(segments[:max_chunks])
    ]


def index_file(
    conn: sqlite3.Connection,
    candidate: ScanCandidate,
    cfg: dict[str, Any],
    *,
    force: bool = False,
    embedder: Embedder | None = None,
    allow_image: bool = True,
) -> tuple[str, int, int]:
    """Index a single candidate.

    Returns ``(status, chunks_written, vectors_written)`` where status is one of
    ``unchanged|touched|reindexed|unsupported|error``.
    """
    index_cfg = cfg["index"]
    path = _canonical(candidate.path)
    row = conn.execute("SELECT * FROM files WHERE path = ?", (path,)).fetchone()

    if (
        row is not None
        and not force
        and row["state"] == "ok"
        and row["size"] == candidate.size
        and row["mtime_ns"] == candidate.mtime_ns
    ):
        return "unchanged", 0, 0

    try:
        digest = hash_file(candidate.path)
    except OSError as exc:
        log.warning("cannot hash %s: %s", candidate.path, exc)
        with conn:
            _upsert_file(conn, path, candidate, None, state="error", error=str(exc))
        return "error", 0, 0

    if row is not None and not force and row["state"] == "ok" and row["hash"] == digest:
        with conn:
            conn.execute(
                "UPDATE files SET size=?, mtime_ns=?, updated_at=? WHERE id=?",
                (candidate.size, candidate.mtime_ns, _now(), row["id"]),
            )
        return "touched", 0, 0

    media_cfg = cfg.get("media") or {}
    media_images = bool(media_cfg.get("images"))
    media_video = bool(media_cfg.get("video"))
    media_audio = bool(media_cfg.get("audio"))
    suffix = candidate.path.suffix.lower()
    is_image = suffix in IMAGE_SUFFIXES
    is_video = suffix in VIDEO_SUFFIXES
    is_audio = suffix in asr.AUDIO_SUFFIXES

    limit = (
        index_cfg["max_pdf_file_size_mb"] if candidate.path.suffix.lower() == ".pdf"
        else index_cfg["max_file_size_mb"]
    ) * 1024 * 1024

    chunks: list[Chunk] = []
    vectors: Sequence[Sequence[float] | None] | None = None
    try:
        if is_video and media_video:
            if not (ffmpeg_available() and ffprobe_available()):
                raise UnsupportedFile(f"video indexing needs ffmpeg: {candidate.path}")
            chunks, vectors = _index_video_frames(
                candidate.path,
                media_cfg,
                embedder,
                allow_image=allow_image,
                ocr=bool(media_cfg.get("video_ocr")),
                ocr_langs=index_cfg["ocr_langs"],
            )
            if not chunks:
                raise UnsupportedFile(f"no frames extracted: {candidate.path}")
        elif is_audio and media_audio:
            if not asr.asr_available(media_cfg.get("whisper_binary")):
                raise UnsupportedFile(
                    f"audio transcription needs whisper.cpp (whisper-cli) and ffmpeg: "
                    f"{candidate.path}"
                )
            chunks = _audio_chunks(
                candidate.path, media_cfg, index_cfg["max_chunks_per_file"]
            )
            # Spoken text is normal text: embed it like any document.
            if embedder is not None and chunks:
                vectors = _embed(
                    embedder, [(chunk.body, candidate.path.stem) for chunk in chunks]
                )
        else:
            extracted = extract(
                candidate.path,
                max_bytes=limit,
                ocr=index_cfg["ocr"],
                ocr_images=index_cfg["ocr_images"],
                media_images=media_images,
                ocr_langs=index_cfg["ocr_langs"],
                ocr_dpi=index_cfg["ocr_dpi"],
                ocr_min_chars=index_cfg["ocr_min_chars"],
                ocr_max_pages=index_cfg["ocr_max_pages"],
                ocr_psm=index_cfg["ocr_psm"],
                ocr_osd=index_cfg["ocr_osd"],
                ocr_workers=index_cfg["ocr_workers"],
            )
            if is_image and media_images:
                # One image = one chunk/vector, whatever the OCR text length is.
                text = extracted.pages[0] if extracted.pages else ""
                chunks = [_image_chunk(text)]
            else:
                chunks = chunk_extracted(extracted)[: index_cfg["max_chunks_per_file"]]
            # Embed before touching the database: never hold the write lock during I/O.
            if embedder is not None and chunks:
                if is_image and media_images:
                    if allow_image:
                        vectors = _embed_image(embedder, candidate.path, media_cfg)
                else:
                    vectors = _embed(
                        embedder, [(chunk.body, candidate.path.stem) for chunk in chunks]
                    )
    except UnsupportedFile as exc:
        with conn:
            _upsert_file(conn, path, candidate, digest, state="unsupported", error=str(exc))
        return "unsupported", 0, 0
    except ExtractionError as exc:
        log.warning("extraction failed for %s: %s", candidate.path, exc)
        with conn:
            _upsert_file(conn, path, candidate, digest, state="error", error=str(exc))
        return "error", 0, 0
    except MediaError as exc:
        log.warning("media extraction failed for %s: %s", candidate.path, exc)
        with conn:
            _upsert_file(conn, path, candidate, digest, state="error", error=str(exc))
        return "error", 0, 0
    except asr.AsrError as exc:
        log.warning("transcription failed for %s: %s", candidate.path, exc)
        with conn:
            _upsert_file(conn, path, candidate, digest, state="error", error=str(exc))
        return "error", 0, 0

    if embedder is not None:
        db.ensure_vec_table(conn, embedder.dim, model_id=cfg["embeddings"]["model"])

    with conn:
        existing = conn.execute("SELECT id FROM files WHERE path = ?", (path,)).fetchone()
        if existing is not None:
            old_ids = _delete_file_chunks(conn, existing["id"])
            if old_ids and db.has_vec_table(conn):
                vec_delete(conn, old_ids)
        file_id = _upsert_file(conn, path, candidate, digest, state="ok", error=None)
        chunk_ids = _write_chunks(conn, file_id, chunks)
        inserted = 0
        embedded = 0
        if vectors is not None:
            pairs = [
                (chunk_id, vector)
                for chunk_id, vector in zip(chunk_ids, vectors, strict=True)
                if vector is not None
            ]
            inserted = vec_insert(conn, pairs)
            embedded = 1 if len(pairs) == len(chunk_ids) else 0
        conn.execute("UPDATE files SET embedded=? WHERE id=?", (embedded, file_id))

    return "reindexed", len(chunks), inserted


# Indirections keep the indexer's coupling to sqlite-vec in one place and make
# the pipeline easy to test with a stub.
def vec_insert(conn: sqlite3.Connection, items: Sequence[tuple[int, Sequence[float]]]) -> int:
    from ..search import vec

    return vec.insert_vectors(conn, items)


def vec_delete(conn: sqlite3.Connection, chunk_ids: list[int]) -> None:
    from ..search import vec

    vec.delete_vectors(conn, chunk_ids)


#: Consecutive "server unavailable" failures that end a backfill pass. A dead
#: server fails every file and each attempt pays the client timeout, so walking a
#: large queue would only spin; content-specific failures never stop the pass.
_BACKFILL_SERVER_FAILURES = 3


def backfill(
    conn: sqlite3.Connection, cfg: dict[str, Any], embedder: Embedder, *, limit: int | None = None
) -> int:
    """Embed chunks of files that are marked ``embedded=0``. Returns file count.

    One file that cannot be embedded no longer aborts the whole pass. A
    content-specific failure (a video whose frames do not match its chunks, an
    image the model refuses, a chunk the server rejects) leaves the file at
    ``embedded=0`` so a later pass retries it, and the walk goes on. Only a run
    of ``ServerUnavailable`` failures ends the pass early: that is what a dead
    embeddings server looks like, and the rest of the queue waits for the next
    pass instead of paying a timeout per file.
    """
    rows = conn.execute(
        "SELECT id, path FROM files WHERE state = 'ok' AND embedded = 0 ORDER BY id"
    ).fetchall()
    done = 0
    pending = 0
    server_failures = 0
    for row in rows:
        chunks = conn.execute(
            "SELECT id, body FROM chunks WHERE file_id = ? ORDER BY seq", (row["id"],)
        ).fetchall()
        if not chunks:
            with conn:
                conn.execute("UPDATE files SET embedded = 1 WHERE id = ?", (row["id"],))
            continue

        media_cfg = cfg.get("media") or {}
        suffix = Path(row["path"]).suffix.lower()
        unavailable = False
        problem: str | None = None
        if suffix in VIDEO_SUFFIXES and media_cfg.get("video"):
            _frame_chunks, vectors = _index_video_frames(
                Path(row["path"]), media_cfg, embedder, allow_image=True, ocr=False
            )
            if vectors is None:
                unavailable, problem = True, "frame embedding failed"
            elif len(vectors) != len(chunks):
                problem = f"only {len(vectors)} of {len(chunks)} frames got a vector"
        elif suffix in IMAGE_SUFFIXES and media_cfg.get("images"):
            vectors = _embed_image(embedder, Path(row["path"]), media_cfg)
            if vectors is None:
                problem = "image embedding failed"
        else:
            vectors = _embed(
                embedder, [(chunk["body"], Path(row["path"]).stem) for chunk in chunks]
            )
            if vectors is None:
                unavailable, problem = True, "embeddings server unavailable"

        if problem is not None:
            pending += 1
            log.warning("backfill: %s: %s", row["path"], problem)
            server_failures = server_failures + 1 if unavailable else 0
            if server_failures >= _BACKFILL_SERVER_FAILURES:
                log.warning(
                    "backfill: embeddings server looks down; stopping this pass, "
                    "the remaining files wait for the next one"
                )
                break
            continue
        server_failures = 0

        # A file only gets here with usable vectors: every way of ending up with
        # ``None`` set ``problem`` above and either continued or stopped the pass.
        assert vectors is not None

        db.ensure_vec_table(conn, embedder.dim, model_id=cfg["embeddings"]["model"])
        chunk_ids = [chunk["id"] for chunk in chunks]
        pairs = [
            (chunk_id, vector)
            for chunk_id, vector in zip(chunk_ids, vectors, strict=True)
            if vector is not None
        ]
        complete = len(pairs) == len(chunk_ids)
        with conn:
            vec_delete(conn, chunk_ids)
            vec_insert(conn, pairs)
            conn.execute(
                "UPDATE files SET embedded = ? WHERE id = ?",
                (1 if complete else 0, row["id"]),
            )
        if complete:
            done += 1
        else:
            pending += 1
            log.warning("some chunks of %s could not be embedded", row["path"])
        if limit is not None and done >= limit:
            break
    if pending:
        log.warning("backfill: %d file(s) still lack vectors, will retry", pending)
    return done


def _build_scanner(cfg: dict[str, Any]) -> Scanner:
    index_cfg = cfg["index"]
    media_cfg = cfg.get("media") or {}
    media_images = bool(media_cfg.get("images"))
    media_video = bool(media_cfg.get("video"))
    media_audio = bool(media_cfg.get("audio"))
    include_ext = list(index_cfg["include_ext"])
    if index_cfg["ocr_images"] or media_images:
        # Images are only worth indexing when OCR or image embeddings are on.
        include_ext += [ext for ext in IMAGE_SUFFIXES if ext not in include_ext]
    if media_video:
        include_ext += [ext for ext in VIDEO_SUFFIXES if ext not in include_ext]
    if media_audio:
        include_ext += [ext for ext in asr.AUDIO_SUFFIXES if ext not in include_ext]
    return Scanner(
        include_ext=include_ext,
        exclude_dirs=index_cfg["exclude_dirs"],
        use_gitignore=index_cfg["use_gitignore"],
        max_file_size_mb=index_cfg["max_file_size_mb"],
        max_pdf_file_size_mb=index_cfg["max_pdf_file_size_mb"],
        follow_symlinks=index_cfg["follow_symlinks"],
        exclude_paths=[paths.data_dir()],
    )


def wipe(conn: sqlite3.Connection) -> None:
    """Remove all indexed content (used by ``index --rebuild``)."""
    with conn:
        conn.execute("DELETE FROM chunks_fts")
        conn.execute("DELETE FROM chunks")
        conn.execute("DELETE FROM files")
    if db.has_vec_table(conn):
        db.drop_vec_table(conn)


def _apply_outcome(
    stats: IndexStats, outcome: str, written: int, inserted: int, path: Path
) -> None:
    """Record one file's result in ``stats`` (shared by both pass kinds)."""
    if outcome == "unchanged":
        stats.unchanged += 1
    elif outcome == "touched":
        stats.touched += 1
    elif outcome == "reindexed":
        stats.reindexed += 1
        stats.chunks += written
        stats.vectors += inserted
    elif outcome == "unsupported":
        stats.unsupported += 1
    elif outcome == "error":
        stats.errors += 1
        if len(stats.error_samples) < 5:
            stats.error_samples.append(str(path))


def _remove_row(conn: sqlite3.Connection, file_id: int) -> int:
    """Delete one file row (its chunks, FTS rows and vectors); returns ``1``."""
    with conn:
        old_ids = _delete_file_chunks(conn, file_id)
        if old_ids and db.has_vec_table(conn):
            vec_delete(conn, old_ids)
        conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
    return 1


def _remove_path(conn: sqlite3.Connection, path: Path) -> int:
    """Delete the index rows for one path; ``1`` when something was removed."""
    row = conn.execute("SELECT id FROM files WHERE path = ?", (_canonical(path),)).fetchone()
    if row is None:
        return 0
    return _remove_row(conn, row["id"])


def _within(path: Path, roots: list[Path]) -> bool:
    """Whether ``path`` is one of ``roots`` or lives under one of them."""
    return any(path == root or root in path.parents for root in roots)


def _prune_missing(
    conn: sqlite3.Connection,
    *,
    scanned: list[Path],
    configured: list[Path],
    seen: set[str],
    stats: IndexStats,
) -> None:
    """Reconcile the index with the configured roots.

    A row goes away when the scanner did not yield it and either it lies under a
    root that was scanned (the file is gone or a config change now filters it
    out) or it lies outside every configured root (its root was dropped from
    ``[index].roots``). A file under a configured root that is currently
    unavailable (an unmounted drive) is kept, so a temporary outage does not
    empty the index.
    """
    for row in conn.execute("SELECT id, path FROM files").fetchall():
        stored = row["path"]
        if stored in seen:
            continue
        path = Path(stored)
        if _within(path, scanned) or not _within(path, configured):
            stats.pruned += _remove_row(conn, row["id"])


def index_roots(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    *,
    roots: list[str] | None = None,
    full: bool = False,
    rebuild: bool = False,
    embedder: Embedder | None = None,
    pause_check: Callable[[], str | None] | None = None,
) -> IndexStats:
    """Run one indexing pass over the configured (or given) roots.

    ``pause_check`` is consulted before every file; when it returns a reason the
    run stops early and the reason is recorded in the stats.
    """
    if rebuild:
        wipe(conn)
    if embedder is not None:
        db.ensure_vec_table(conn, embedder.dim, model_id=cfg["embeddings"]["model"])

    root_list = roots if roots is not None else cfg["index"]["roots"]
    active = [
        Path(os.path.abspath(os.path.expanduser(str(root))))
        for root in root_list
        if Path(os.path.abspath(os.path.expanduser(str(root)))).is_dir()
    ]
    stats = IndexStats()
    scanner = _build_scanner(cfg)
    seen: set[str] = set()

    media_cfg = cfg.get("media") or {}
    media_images = bool(media_cfg.get("images"))
    media_video = bool(media_cfg.get("video"))
    image_budget = int(media_cfg.get("images_per_run", 0))
    limit_media = (media_images or media_video) and image_budget > 0

    for candidate in scanner.iter_files(active):
        if pause_check is not None:
            reason = pause_check()
            if reason:
                stats.paused = reason
                log.info("indexing paused: %s", reason)
                break
        stats.scanned += 1
        seen.add(_canonical(candidate.path))
        suffix = candidate.path.suffix.lower()
        is_media = (suffix in IMAGE_SUFFIXES and media_images) or (
            suffix in VIDEO_SUFFIXES and media_video
        )
        allow_image = (not is_media) or (not limit_media) or image_budget > 0
        outcome, written, inserted = index_file(
            conn, candidate, cfg, force=full, embedder=embedder, allow_image=allow_image
        )
        if is_media and limit_media and outcome == "reindexed" and inserted > 0:
            image_budget -= 1
        _apply_outcome(stats, outcome, written, inserted, candidate.path)

    # A pass over the configured roots is authoritative: once it finished, the
    # index is reconciled with the config. Explicit roots (``feagle index
    # --root``, benchmarks) and a pass that paused halfway only touch what they
    # actually saw.
    if roots is None and active and not stats.paused:
        scanned = [Path(os.path.realpath(root)) for root in active]
        configured = [
            Path(os.path.realpath(os.path.abspath(os.path.expanduser(str(root)))))
            for root in cfg["index"]["roots"]
        ]
        _prune_missing(conn, scanned=scanned, configured=configured, seen=seen, stats=stats)

    log.info("index run finished: %s", stats.summary())
    return stats


def index_paths(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    paths: Iterable[Path | str],
    *,
    embedder: Embedder | None = None,
    pause_check: Callable[[], str | None] | None = None,
) -> IndexStats:
    """Index exactly ``paths`` (no root walk) and drop the ones that vanished.

    The watcher already knows which files changed, so re-walking every root and
    stat-ing every file in it is wasted work on a large tree. A path that is a
    directory is walked as a small root of its own. Deletions are handled for the
    paths in the batch; anything the watcher misses is caught by the periodic
    full rescan.
    """
    if embedder is not None:
        db.ensure_vec_table(conn, embedder.dim, model_id=cfg["embeddings"]["model"])

    stats = IndexStats()
    scanner = _build_scanner(cfg)
    media_cfg = cfg.get("media") or {}
    media_images = bool(media_cfg.get("images"))
    media_video = bool(media_cfg.get("video"))
    image_budget = int(media_cfg.get("images_per_run", 0))
    limit_media = (media_images or media_video) and image_budget > 0

    for raw in sorted({str(path) for path in paths}):
        if pause_check is not None:
            reason = pause_check()
            if reason:
                stats.paused = reason
                log.info("indexing paused: %s", reason)
                break
        path = Path(os.path.abspath(os.path.expanduser(raw)))
        if path.is_dir():
            candidates: Iterable[ScanCandidate] = scanner.iter_files([path])
        elif not path.exists():
            stats.pruned += _remove_path(conn, path)
            continue
        else:
            candidate = scanner.candidate_for(path)
            candidates = [candidate] if candidate is not None else []

        for candidate in candidates:
            stats.scanned += 1
            suffix = candidate.path.suffix.lower()
            is_media = (suffix in IMAGE_SUFFIXES and media_images) or (
                suffix in VIDEO_SUFFIXES and media_video
            )
            allow_image = (not is_media) or (not limit_media) or image_budget > 0
            outcome, written, inserted = index_file(
                conn, candidate, cfg, embedder=embedder, allow_image=allow_image
            )
            if is_media and limit_media and outcome == "reindexed" and inserted > 0:
                image_budget -= 1
            _apply_outcome(stats, outcome, written, inserted, candidate.path)

    log.info("change pass finished: %s", stats.summary())
    return stats
