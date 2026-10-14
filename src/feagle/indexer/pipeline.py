"""Incremental indexing pipeline.

For every scanned file we compare ``(size, mtime_ns)`` with the database and, if
they differ, hash the content (blake2b). An unchanged hash only refreshes the
stored stat ("touch"); a changed hash triggers extraction and re-chunking in a
single transaction per file. The indexer is the only writer.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import paths
from ..log import get_logger
from .chunker import chunk_extracted
from .extract import ExtractionError, UnsupportedFile, extract
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
    error_samples: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"scanned={self.scanned} reindexed={self.reindexed} "
            f"touched={self.touched} unchanged={self.unchanged} "
            f"unsupported={self.unsupported} errors={self.errors} "
            f"pruned={self.pruned} chunks={self.chunks}"
        )


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


def _delete_file_chunks(conn: sqlite3.Connection, file_id: int) -> None:
    conn.execute(
        "DELETE FROM chunks_fts WHERE chunk_id IN (SELECT id FROM chunks WHERE file_id = ?)",
        (file_id,),
    )
    conn.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))


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


def _write_chunks(conn: sqlite3.Connection, file_id: int, chunks: list[Any]) -> int:
    for chunk in chunks:
        cur = conn.execute(
            "INSERT INTO chunks(file_id, seq, page, line, body) VALUES(?,?,?,?,?)",
            (file_id, chunk.seq, chunk.page, chunk.line, chunk.body),
        )
        conn.execute(
            "INSERT INTO chunks_fts(body, chunk_id) VALUES(?, ?)",
            (chunk.body, cur.lastrowid),
        )
    return len(chunks)


def index_file(
    conn: sqlite3.Connection,
    candidate: ScanCandidate,
    cfg: dict[str, Any],
    *,
    force: bool = False,
) -> tuple[str, int]:
    """Index a single candidate.

    Returns ``(status, chunks_written)`` where status is one of
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
        return "unchanged", 0

    try:
        digest = hash_file(candidate.path)
    except OSError as exc:
        log.warning("cannot hash %s: %s", candidate.path, exc)
        with conn:
            _upsert_file(conn, path, candidate, None, state="error", error=str(exc))
        return "error", 0

    if row is not None and not force and row["state"] == "ok" and row["hash"] == digest:
        with conn:
            conn.execute(
                "UPDATE files SET size=?, mtime_ns=?, updated_at=? WHERE id=?",
                (candidate.size, candidate.mtime_ns, _now(), row["id"]),
            )
        return "touched", 0

    limit = (
        index_cfg["max_pdf_file_size_mb"] if candidate.path.suffix.lower() == ".pdf"
        else index_cfg["max_file_size_mb"]
    ) * 1024 * 1024

    try:
        extracted = extract(candidate.path, max_bytes=limit)
        chunks = chunk_extracted(extracted)[: index_cfg["max_chunks_per_file"]]
    except UnsupportedFile as exc:
        with conn:
            _upsert_file(conn, path, candidate, digest, state="unsupported", error=str(exc))
        return "unsupported", 0
    except ExtractionError as exc:
        log.warning("extraction failed for %s: %s", candidate.path, exc)
        with conn:
            _upsert_file(conn, path, candidate, digest, state="error", error=str(exc))
        return "error", 0

    with conn:
        file_id = _upsert_file(conn, path, candidate, digest, state="ok", error=None)
        written = _write_chunks(conn, file_id, chunks)
    return "reindexed", written


def _build_scanner(cfg: dict[str, Any]) -> Scanner:
    index_cfg = cfg["index"]
    return Scanner(
        include_ext=index_cfg["include_ext"],
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
                _delete_file_chunks(conn, row["id"])
                conn.execute("DELETE FROM files WHERE id = ?", (row["id"],))
            stats.pruned += 1


def index_roots(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    *,
    roots: list[str] | None = None,
    full: bool = False,
    rebuild: bool = False,
) -> IndexStats:
    """Run one indexing pass over the configured (or given) roots."""
    if rebuild:
        wipe(conn)

    root_list = roots if roots is not None else cfg["index"]["roots"]
    active = [
        Path(os.path.abspath(os.path.expanduser(str(root))))
        for root in root_list
        if Path(os.path.abspath(os.path.expanduser(str(root)))).is_dir()
    ]
    stats = IndexStats()
    scanner = _build_scanner(cfg)
    seen: set[str] = set()

    for candidate in scanner.iter_files(active):
        stats.scanned += 1
        seen.add(_canonical(candidate.path))
        outcome, written = index_file(conn, candidate, cfg, force=full)
        if outcome == "unchanged":
            stats.unchanged += 1
        elif outcome == "touched":
            stats.touched += 1
        elif outcome == "reindexed":
            stats.reindexed += 1
            stats.chunks += written
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
