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
import sqlite3
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import db, paths
from ..embeddings.client import EmbeddingError, ServerUnavailable
from ..embeddings.service import Embedder
from ..log import get_logger
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
from .ocr import IMAGE_SUFFIXES
from .scanner import ScanCandidate, Scanner

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
) -> list[list[float] | None] | None:
    """Embed documents, degrading gracefully instead of failing the file.

    Returns a list aligned with ``items`` (``None`` marks a chunk that could not
    be embedded), or ``None`` when the server is unavailable and nothing could
    be embedded at all. A batch that the server rejects (for example an input
    larger than the slot context) is retried chunk by chunk so that one bad
    chunk does not cost the whole file its vectors.
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

    if all(vector is None for vector in vectors):
        return None
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


def _index_video_frames(
    path: Path,
    media_cfg: dict[str, Any],
    embedder: Embedder | None,
    *,
    allow_image: bool,
) -> tuple[list[Chunk], list[list[float]] | None]:
    """Extract frames and build one chunk per frame (with its timestamp)."""
    with tempfile.TemporaryDirectory(prefix="feagle-video-") as tmp:
        frames = extract_frames(
            path,
            Path(tmp),
            interval=float(media_cfg["video_frame_interval"]),
            max_frames=int(media_cfg["video_max_frames"]),
            max_dim=int(media_cfg["image_max_dim"]),
        )
        chunks = [
            Chunk(seq=index, page=None, line=None, body="", time_ms=time_ms)
            for index, (time_ms, _frame) in enumerate(frames)
        ]
        vectors: list[list[float]] | None = None
        if embedder is not None and chunks and allow_image:
            vectors = _embed_prepared(embedder, [frame for _time, frame in frames])
        return chunks, vectors


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
    suffix = candidate.path.suffix.lower()
    is_image = suffix in IMAGE_SUFFIXES
    is_video = suffix in VIDEO_SUFFIXES

    limit = (
        index_cfg["max_pdf_file_size_mb"] if candidate.path.suffix.lower() == ".pdf"
        else index_cfg["max_file_size_mb"]
    ) * 1024 * 1024

    chunks: list[Chunk] = []
    vectors: list[list[float]] | None = None
    try:
        if is_video and media_video:
            if not (ffmpeg_available() and ffprobe_available()):
                raise UnsupportedFile(f"video indexing needs ffmpeg: {candidate.path}")
            chunks, vectors = _index_video_frames(
                candidate.path, media_cfg, embedder, allow_image=allow_image
            )
            if not chunks:
                raise UnsupportedFile(f"no frames extracted: {candidate.path}")
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
def vec_insert(conn: sqlite3.Connection, items: list[tuple[int, list[float]]]) -> int:
    from ..search import vec

    return vec.insert_vectors(conn, items)


def vec_delete(conn: sqlite3.Connection, chunk_ids: list[int]) -> None:
    from ..search import vec

    vec.delete_vectors(conn, chunk_ids)


def backfill(
    conn: sqlite3.Connection, cfg: dict[str, Any], embedder: Embedder, *, limit: int | None = None
) -> int:
    """Embed chunks of files that are marked ``embedded=0``. Returns file count."""
    rows = conn.execute(
        "SELECT id, path FROM files WHERE state = 'ok' AND embedded = 0 ORDER BY id"
    ).fetchall()
    done = 0
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
        if suffix in VIDEO_SUFFIXES and media_cfg.get("video"):
            _frame_chunks, vectors = _index_video_frames(
                Path(row["path"]), media_cfg, embedder, allow_image=True
            )
            if vectors is None or len(vectors) != len(chunks):
                log.warning("cannot backfill video %s", row["path"])
                break
        elif suffix in IMAGE_SUFFIXES and media_cfg.get("images"):
            vectors = _embed_image(embedder, Path(row["path"]), media_cfg)
        else:
            vectors = _embed(
                embedder, [(chunk["body"], Path(row["path"]).stem) for chunk in chunks]
            )
        if vectors is None:
            break  # server unavailable: stop rather than spin

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
            log.warning("some chunks of %s could not be embedded", row["path"])
        if limit is not None and done >= limit:
            break
    return done


def _build_scanner(cfg: dict[str, Any]) -> Scanner:
    index_cfg = cfg["index"]
    media_cfg = cfg.get("media") or {}
    media_images = bool(media_cfg.get("images"))
    media_video = bool(media_cfg.get("video"))
    include_ext = list(index_cfg["include_ext"])
    if index_cfg["ocr_images"] or media_images:
        # Images are only worth indexing when OCR or image embeddings are on.
        include_ext += [ext for ext in IMAGE_SUFFIXES if ext not in include_ext]
    if media_video:
        include_ext += [ext for ext in VIDEO_SUFFIXES if ext not in include_ext]
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


def _prune_missing(
    conn: sqlite3.Connection, roots: list[Path], seen: set[str], stats: IndexStats
) -> None:
    """Delete rows for files that no longer exist under the scanned roots."""
    if not roots:
        return
    for row in conn.execute("SELECT id, path FROM files").fetchall():
        stored = row["path"]
        if stored in seen:
            continue
        path = Path(stored)
        if any(path == root or root in path.parents for root in roots):
            with conn:
                old_ids = _delete_file_chunks(conn, row["id"])
                if old_ids and db.has_vec_table(conn):
                    vec_delete(conn, old_ids)
                conn.execute("DELETE FROM files WHERE id = ?", (row["id"],))
            stats.pruned += 1


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
                stats.error_samples.append(str(candidate.path))

    if not full and not rebuild:
        _prune_missing(conn, active, seen, stats)

    log.info("index run finished: %s", stats.summary())
    return stats
