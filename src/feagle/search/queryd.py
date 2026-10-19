"""The query daemon: a line-delimited JSON protocol over a Unix socket.

One request per line, one response per line. The socket lives in
``$XDG_RUNTIME_DIR/feagle/`` (0700) and is created 0600. Each connection is
handled on its own thread and opens its own read-only database connection, so
no SQLite object is shared across threads.

Everything the daemon can reach comes from the index: ``preview`` refuses a
path that is not a row in ``files``, so being able to talk to the socket does
not turn into being able to read any file of the user (see ``ARCHITECTURE.md``,
"Модель безопасности").
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import socket
import socketserver
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from .. import __version__, db, frecency, gui, history, paths
from ..embeddings.client import EmbeddingError, ServerUnavailable
from ..log import get_logger
from ..search import hybrid
from ..search.vec import vec_count

if TYPE_CHECKING:
    from collections.abc import Callable

log = get_logger("search.queryd")

_EMBEDDINGS_HINT = "start llama-server and run 'feagle setup' to enable semantic search"

#: Largest ranking bonus frecency can add to an application. Kept below the gap
#: between match tiers (1/10/25/50/100), so history reorders within a tier but
#: never lets a weakly matching app outrank a stronger match.
_FRECENCY_BONUS = 5.0

#: Lines of context shown before and after the matched line in a preview.
_PREVIEW_CONTEXT_LINES = 12
#: A preview never reads more than this many bytes of a file.
_PREVIEW_MAX_BYTES = 2 * 1024 * 1024
#: Wall-clock limit for rendering one PDF page for a preview.
_PREVIEW_PDF_TIMEOUT = 20.0
#: How many rendered PDF pages to keep (moving through one page must not re-render).
_PREVIEW_PAGE_CACHE = 8

# -- request limits --------------------------------------------------------
#: A client of the same user is not an attacker (see the security model), but a
#: buggy one must not be able to exhaust the daemon. Every limit below answers
#: with ``ok:false`` rather than truncating silently.

#: Longest single request line (one JSON object) accepted; a longer one is
#: rejected without buffering the rest of it.
_MAX_REQUEST_BYTES = 256 * 1024
#: Longest search query accepted, in characters.
_MAX_QUERY_CHARS = 4096
#: How many texts one ``embed`` request may carry, and how long each may be.
_MAX_EMBED_TEXTS = 64
_MAX_EMBED_CHARS = 8192
#: Seconds a connection may sit idle before the daemon closes it. Clients
#: reconnect (the QML popup does so on its own), and a stray client that opens a
#: connection and never sends anything cannot hold the slot forever.
_CLIENT_TIMEOUT = 120.0
#: Connections served at the same time; the rest are refused right away instead
#: of piling up threads.
_MAX_CONNECTIONS = 16


#: Search modes the protocol accepts (the same three the CLI offers).
_MODES = ("auto", "hybrid", "fts")

#: What ``used`` may report: an application launch or an opened file.
_KINDS = ("app", "file")

#: How many pinned/recent files browse mode (an empty query) returns.
_BROWSE_FILES = 10


class _BadRequest(Exception):
    """A request field has the wrong type or an unusable value."""


def _as_int(
    value: Any, name: str, default: int | None = None, *, minimum: int | None = None
) -> int | None:
    """``value`` as an integer, rejecting anything that is not one.

    A JSON client normally sends a number, but a buggy one (or a hand-written
    ``nc``) can send ``"10"`` or ``"abc"``; ``"abc"`` used to reach SQLite and
    raise a ``TypeError`` deep in a query builder. A numeric string is accepted,
    everything else is answered with a clear error.
    """
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise _BadRequest(f"{name} must be an integer")
    try:
        number = int(value)
    except ValueError:
        raise _BadRequest(f"{name} must be an integer") from None
    if minimum is not None and number < minimum:
        raise _BadRequest(f"{name} must be >= {minimum}")
    return number


def _as_count(value: Any, name: str, default: int, *, minimum: int = 1) -> int:
    """An integer with a fallback for a missing field (never ``None``)."""
    number = _as_int(value, name, default, minimum=minimum)
    return default if number is None else number


def _as_text_list(value: Any, name: str) -> list[str]:
    """``value`` as a list of strings; a bare string is *not* a list of chars."""
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise _BadRequest(f"{name} must be a list of strings")
    return value


def _as_mode(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in _MODES:
        raise _BadRequest("mode must be one of " + ", ".join(_MODES))
    return value


def _as_kind(value: Any) -> str:
    """What ``used`` came from; applications unless told otherwise."""
    if value is None:
        return "app"
    if not isinstance(value, str) or value not in _KINDS:
        raise _BadRequest("kind must be one of " + ", ".join(_KINDS))
    return value


def _preview_window(total: int, line: int | None) -> tuple[int, int]:
    """The ``[start, end)`` line range to show for a preview."""
    if line is None or line < 1:
        return 0, min(total, _PREVIEW_CONTEXT_LINES * 2)
    index = line - 1
    return (
        max(0, index - _PREVIEW_CONTEXT_LINES),
        min(total, index + _PREVIEW_CONTEXT_LINES + 1),
    )


def _find_anchor(lines: list[str], needle: str) -> int | None:
    """The 1-based line of ``needle``'s first non-empty line, or ``None``."""
    wanted = next((part.strip() for part in needle.splitlines() if part.strip()), "")
    if not wanted:
        return None
    head = wanted[:60]
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == wanted or stripped.startswith(head) or head.startswith(stripped[:60]):
            return number
    return None


class QueryEngine:
    """Executes protocol operations against the index."""

    def __init__(
        self,
        cfg: dict[str, Any],
        *,
        db_path: Path | None = None,
        embedder: Any | None = None,
        embedder_provider: Callable[[], Any] | None = None,
        on_embedder_failure: Callable[[], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
        retry_cooldown: float = 30.0,
        frecency_store: Any | None = None,
        history_store: Any | None = None,
    ) -> None:
        self.cfg = cfg
        self.db_path = db_path or paths.db_path()
        self.embedder = embedder
        self._provider = embedder_provider
        self._on_failure = on_embedder_failure
        self._clock = clock
        self._retry_cooldown = retry_cooldown
        self._retry_after = 0.0
        self._warming = False
        self._apps_cache: list[Any] | None = None
        self._apps_built = 0.0
        self._apps_ttl = 60.0
        self._frecency = frecency_store if frecency_store is not None else frecency.FrecencyStore()
        self._history = (
            history_store if history_store is not None else history.QueryHistory()
        )
        self._page_cache: dict[tuple[str, int, int], list[str]] = {}

    def _ensure_embedder(self) -> Any | None:
        """Return an embedder, (re)starting it on demand with a cooldown."""
        if self.embedder is not None:
            return self.embedder
        if self._provider is None or self._clock() < self._retry_after:
            return None
        try:
            self.embedder = self._provider()
        except Exception as exc:  # noqa: BLE001 - any failure means "text only"
            log.warning("embeddings unavailable: %s", exc)
            self._retry_after = self._clock() + self._retry_cooldown
            return None
        return self.embedder

    def _drop_embedder(self) -> None:
        """Forget a broken embedder so the next request can restart it."""
        self.embedder = None
        self._retry_after = self._clock() + self._retry_cooldown
        if self._on_failure is not None:
            with contextlib.suppress(Exception):
                self._on_failure()

    def _revive_embedder(self) -> Any | None:
        """Restart the embedder right away (used when the server just died).

        The retry cooldown exists to avoid hammering a broken setup, but a server
        that was unloaded for being idle should come back on the same request.
        """
        if self._provider is None:
            return None
        self.embedder = None
        self._retry_after = 0.0
        return self._ensure_embedder()

    def _connect(self):
        return db.connect(self.db_path, read_only=True)

    # -- operations --------------------------------------------------------

    def ping(self) -> dict[str, Any]:
        return {"ok": True, "version": __version__}

    def warmup(self) -> dict[str, Any]:
        """Start the embedder in the background; never block the caller."""
        if self.embedder is not None:
            return {"ok": True, "warmed": True}
        if self._provider is None:
            return {"ok": False, "error": "embeddings unavailable", "hint": _EMBEDDINGS_HINT}
        if not self._warming:
            self._warming = True
            threading.Thread(
                target=self._warmup_worker, name="feagle-warmup", daemon=True
            ).start()
        return {"ok": True, "warming": True}

    def _warmup_worker(self) -> None:
        try:
            self._ensure_embedder()
        finally:
            self._warming = False

    def _app_list(self) -> list[Any]:
        now = self._clock()
        if self._apps_cache is None or now - self._apps_built > self._apps_ttl:
            from .. import apps as apps_module

            self._apps_cache = apps_module.discover_apps()
            self._apps_built = now
        return self._apps_cache

    def _app_bonus(self) -> Callable[[Any], float]:
        """A bounded frecency bonus for ranking applications."""
        scores = self._frecency.scores()
        if not scores:
            return lambda app: 0.0

        def bonus(app: Any) -> float:
            value = scores.get(app.id, 0.0)
            return min(value, frecency.SATURATION) / frecency.SATURATION * _FRECENCY_BONUS

        return bonus

    def launcher(
        self,
        query: str,
        *,
        limit: int | None = None,
        mode: str | None = None,
        apps_limit: int = 8,
    ) -> dict[str, Any]:
        """A ready-to-render list: applications first, then file hits."""
        from .. import apps as apps_module

        if (rejection := self._over_limit("query", query, _MAX_QUERY_CHARS)) is not None:
            return rejection
        # An empty query is browse mode: no search (and no embedding of ""), just
        # applications plus the pinned/recent files.
        if query.strip():
            files = self.search(query, limit=limit, mode=mode)
        else:
            files = {"ok": True, "mode": "fts", "took_ms": 0.0, "results": []}
        results: list[dict[str, Any]] = []
        for score, app in apps_module.match_apps(
            self._app_list(), query, limit=apps_limit, bonus=self._app_bonus()
        ):
            results.append({
                "kind": "app",
                "id": app.id,
                "name": app.name,
                "score": score,
                "icon": app.icon,
                "exec": apps_module.launch_command(app),
            })
        for item in files["results"]:
            entry = dict(item)
            entry["kind"] = "file"
            results.append(entry)
        if not query.strip():
            # Browse mode: pinned files first, then what was opened recently.
            for item in self._browse_files(_BROWSE_FILES):
                entry = dict(item)
                entry["kind"] = "file"
                results.append(entry)
        return {
            "ok": True,
            "mode": files["mode"],
            "took_ms": files["took_ms"],
            "results": results,
        }

    def used(self, key: str, kind: str = "app") -> dict[str, Any]:
        """Remember a launch (feeds application ranking and recent files).

        A file is recorded only when the index knows it: the frecency store is
        user-writable, and browse mode shows those paths back as "recent files",
        so anything else would turn it into a way to surface arbitrary paths.
        """
        if kind == "file" and not self._indexed(Path(key)):
            return {"ok": False, "error": f"not in the index: {key}"}
        self._frecency.record(key)
        return {"ok": True}

    def history(self, add: Any = None) -> dict[str, Any]:
        """Recent queries; with ``add``, remember one first."""
        if add is None:
            return {"ok": True, "entries": self._history.entries()}
        if not isinstance(add, str):
            raise _BadRequest("add must be a string")
        return {"ok": True, "entries": self._history.add(add)}

    def _browse_files(self, limit: int) -> list[dict[str, Any]]:
        """Pinned files first, then files opened recently (browse mode).

        Both sources are user-writable (``gui.toml`` and ``frecency.json``), so
        every candidate is checked against the index before it is returned.
        """
        pinned = [path for path in gui.load_gui().get("favorites", []) if path]
        scores = self._frecency.scores()
        recent = sorted(
            (key for key in scores if key.startswith("/")),
            key=lambda key: scores[key],
            reverse=True,
        )
        candidates = list(dict.fromkeys([*pinned, *recent]))
        kept = self._indexed_paths(candidates[: limit + len(pinned)])
        return [
            {
                "rank": rank,
                "score": round(scores.get(path, 0.0), 4),
                "path": path,
                "page": None,
                "line": None,
                "time": None,
                "snippet": "",
                "chunk_id": None,
            }
            for rank, path in enumerate(kept[:limit], start=1)
        ]

    def _indexed_paths(self, paths_: list[str]) -> list[str]:
        """The subset of ``paths_`` the index knows, in the same order.

        One connection for the whole batch: browse mode asks for a couple dozen
        paths at once, and ``files.path`` is unique, so each check is an index
        hit. Both spellings are accepted, like `_indexed`.
        """
        if not paths_:
            return []
        try:
            conn = self._connect()
        except (FileNotFoundError, sqlite3.Error) as exc:
            log.warning("browse: cannot consult the index: %s", exc)
            return []
        try:
            kept: list[str] = []
            for path in paths_:
                row = conn.execute(
                    "SELECT 1 FROM files WHERE path IN (?, ?) LIMIT 1",
                    (path, os.path.realpath(path)),
                ).fetchone()
                if row is not None:
                    kept.append(path)
            return kept
        finally:
            conn.close()

    def _indexed(self, target: Path) -> bool:
        """Whether ``target`` is a file the index knows about.

        ``preview`` followed the path it was handed, so any local process that
        could open the 0600 socket could read *any* file of the user through it.
        The index is the natural allowlist: every path that can show up in a
        reply was put there by the indexer, and nothing else is reachable.

        The indexer stores ``realpath`` (``pipeline._canonical``), so a path
        reached through a symlinked root would not match its own row; both
        spellings of ``target`` are accepted.
        """
        try:
            conn = self._connect()
        except (FileNotFoundError, sqlite3.Error) as exc:
            log.warning("preview: cannot consult the index: %s", exc)
            return False
        wanted = (str(target), os.path.realpath(target))
        placeholders = ", ".join("?" * len(wanted))
        try:
            row = conn.execute(
                f"SELECT 1 FROM files WHERE path IN ({placeholders}) LIMIT 1", wanted
            ).fetchone()
        except sqlite3.Error as exc:
            log.warning("preview: index lookup failed: %s", exc)
            return False
        finally:
            conn.close()
        return row is not None

    def preview(
        self,
        path: str,
        *,
        line: int | None = None,
        page: int | None = None,
        chunk_id: int | None = None,
    ) -> dict[str, Any]:
        """A small preview of a file: text around a line, or an image marker.

        The client renders ``kind == "image"`` by loading the file itself and
        ``kind == "text"`` as monospace context; anything unreadable or binary
        comes back as ``kind == "meta"`` with the file's size and mtime.

        A PDF has no line numbers (a chunk carries only its page), so with
        ``page`` the page is rendered and the window is anchored on the chunk's
        own text when ``chunk_id`` is given.

        Only files present in the index are served (see ``_indexed``); the path
        is accepted both as given (``abspath`` + ``expanduser``) and resolved
        (``realpath``), which is the form the indexer stores.
        """
        from ..indexer import extract

        target = Path(os.path.abspath(os.path.expanduser(str(path))))
        if not self._indexed(target):
            return {"ok": False, "error": f"not in the index: {path}"}
        if target.is_symlink() and not self.cfg.get("index", {}).get("follow_symlinks", False):
            # The indexer would not have indexed this path as a symlink.
            return {"ok": False, "error": f"not in the index: {path}"}
        try:
            stat = target.stat()
        except OSError as exc:
            return {"ok": False, "error": f"cannot read {path}: {exc}"}
        if not target.is_file():
            return {"ok": False, "error": "not a regular file"}
        base = {"path": str(target), "size": stat.st_size, "mtime": stat.st_mtime}
        suffix = target.suffix.lower()
        if suffix in extract.IMAGE_SUFFIXES:
            return {"ok": True, "kind": "image", **base}
        if suffix == ".pdf" and page is not None:
            return self._preview_pdf(target, page, stat.st_mtime_ns, chunk_id, base)
        try:
            extracted = extract.extract_text(target, max_bytes=_PREVIEW_MAX_BYTES)
        except extract.UnsupportedFile as exc:
            return {"ok": True, "kind": "meta", **base, "reason": str(exc)}
        except extract.ExtractionError as exc:
            return {"ok": False, "error": str(exc)}
        text = extracted.pages[0] if extracted.pages else ""
        lines = text.splitlines()
        start, end = _preview_window(len(lines), line)
        return {
            "ok": True,
            "kind": "text",
            **base,
            "line": line,
            "start_line": start + 1,
            "total_lines": len(lines),
            "text": "\n".join(lines[start:end]),
            "truncated": stat.st_size > _PREVIEW_MAX_BYTES,
        }

    def _preview_pdf(
        self,
        target: Path,
        page: int,
        mtime_ns: int,
        chunk_id: int | None,
        base: dict[str, Any],
    ) -> dict[str, Any]:
        """The text of one PDF page, centred on the chunk that matched."""
        lines = self._pdf_page_lines(target, page, mtime_ns)
        if lines is None:
            return {
                "ok": True,
                "kind": "meta",
                **base,
                "page": page,
                "reason": "cannot read the page text (is poppler installed?)",
            }
        anchor = None
        if chunk_id is not None:
            body = self._chunk_body(int(chunk_id))
            if body:
                anchor = _find_anchor(lines, body)
        start, end = _preview_window(len(lines), anchor)
        return {
            "ok": True,
            "kind": "text",
            **base,
            "page": page,
            "line": anchor,
            "start_line": start + 1,
            "total_lines": len(lines),
            "text": "\n".join(lines[start:end]),
            "truncated": False,
        }

    def _pdf_page_lines(self, target: Path, page: int, mtime_ns: int) -> list[str] | None:
        """One PDF page's text lines, cached per (file, mtime, page)."""
        key = (str(target), mtime_ns, page)
        cached = self._page_cache.get(key)
        if cached is not None:
            return cached
        binary = shutil.which("pdftotext")
        if binary is None:
            return None
        try:
            proc = subprocess.run(
                [
                    binary, "-enc", "UTF-8", "-q",
                    "-f", str(page), "-l", str(page), "--", str(target), "-",
                ],
                capture_output=True,
                timeout=_PREVIEW_PDF_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("cannot render %s page %d: %s", target, page, exc)
            return None
        if proc.returncode != 0:
            detail = proc.stderr.decode("utf-8", errors="replace").strip()
            log.warning("pdftotext failed for %s page %d: %s", target, page, detail[:200])
            return None
        lines = proc.stdout.decode("utf-8", errors="replace").splitlines()
        if len(self._page_cache) >= _PREVIEW_PAGE_CACHE:
            self._page_cache.pop(next(iter(self._page_cache)))
        self._page_cache[key] = lines
        return lines

    def _chunk_body(self, chunk_id: int) -> str | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT body FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
        finally:
            conn.close()
        return row["body"] if row is not None else None

    def _over_limit(self, name: str, value: str, maximum: int) -> dict[str, Any] | None:
        """A rejection reply when ``value`` is longer than ``maximum``, else ``None``."""
        if len(value) > maximum:
            return {
                "ok": False,
                "error": f"{name} too long: {len(value)} > {maximum} characters",
            }
        return None

    def search(
        self, query: str, *, limit: int | None = None, mode: str | None = None
    ) -> dict[str, Any]:
        if (rejection := self._over_limit("query", query, _MAX_QUERY_CHARS)) is not None:
            return rejection
        search_cfg = self.cfg["search"]
        limit = limit or search_cfg["limit"]
        mode = mode or search_cfg["mode"]
        stem = search_cfg["stemming"] != "none"
        started = time.perf_counter()

        conn = self._connect()
        try:
            embedder = self._ensure_embedder()
            vector: list[float] | None = None
            if mode in ("auto", "hybrid") and embedder is not None and db.has_vec_table(conn):
                try:
                    vector = embedder.embed_queries([query])[0]
                except (ServerUnavailable, EmbeddingError) as exc:
                    log.warning("query embedding failed (%s); restarting the server", exc)
                    self._drop_embedder()
                    embedder = self._revive_embedder()
                    if embedder is not None:
                        try:
                            vector = embedder.embed_queries([query])[0]
                        except (ServerUnavailable, EmbeddingError) as retry_exc:
                            log.warning("still unavailable (%s); text search only", retry_exc)
                            self._drop_embedder()
                            vector = None
            if vector is not None:
                hits = hybrid.search_hybrid(
                    conn,
                    query,
                    query_vector=vector,
                    limit=limit,
                    candidates=search_cfg["candidates"],
                    rrf_k=search_cfg["rrf_k"],
                    stem=stem,
                    max_distance=hybrid.effective_max_distance(self.cfg),
                    max_per_file=search_cfg["max_per_file"],
                )
                used = "hybrid"
            else:
                hits = hybrid.search_text(
                    conn, query, limit=limit,
                    candidates=search_cfg["candidates"], rrf_k=search_cfg["rrf_k"],
                    stem=stem, max_per_file=search_cfg["max_per_file"],
                )
                used = "fts"
        finally:
            conn.close()

        took_ms = round((time.perf_counter() - started) * 1000, 1)
        return {
            "ok": True,
            "mode": used,
            "took_ms": took_ms,
            "results": [
                {
                    "rank": rank,
                    "score": hit.score,
                    "path": hit.path,
                    "page": hit.page,
                    "line": hit.line,
                    "time": hit.time_ms / 1000 if hit.time_ms is not None else None,
                    "snippet": hit.snippet,
                    "chunk_id": hit.chunk_id,
                }
                for rank, hit in enumerate(hits, start=1)
            ],
        }

    def embed(
        self, texts: list[str], kind: str = "doc", titles: list[str] | None = None
    ) -> dict[str, Any]:
        if len(texts) > _MAX_EMBED_TEXTS:
            return {
                "ok": False,
                "error": f"too many texts: {len(texts)} > {_MAX_EMBED_TEXTS}",
            }
        for text in texts:
            if (rejection := self._over_limit("text", text, _MAX_EMBED_CHARS)) is not None:
                return rejection
        embedder = self._ensure_embedder()
        if embedder is None:
            return {"ok": False, "error": "embeddings unavailable", "hint": _EMBEDDINGS_HINT}
        try:
            if kind == "query":
                vectors = embedder.embed_queries(texts)
            else:
                title_list = list(titles) if titles else []
                items = [
                    (text, title_list[index] if index < len(title_list) else None)
                    for index, text in enumerate(texts)
                ]
                vectors = embedder.embed_documents(items)
        except (ServerUnavailable, EmbeddingError) as exc:
            self._drop_embedder()
            return {"ok": False, "error": str(exc), "hint": _EMBEDDINGS_HINT}
        return {"ok": True, "dim": embedder.dim, "embeddings": vectors}

    def get_gui(self) -> dict[str, Any]:
        """The popup's settings, plus the schema its panel renders from.

        The schema travels with the values so the QML side does not repeat the
        ranges and choices: `gui.SETTINGS` stays the single source of truth.
        """
        return {
            "ok": True,
            "gui": gui.load_gui(),
            "schema": gui.schema(),
            "path": str(gui.gui_file()),
        }

    def set_gui(self, settings: Any) -> dict[str, Any]:
        """Validate and persist a partial settings change, all-or-nothing."""
        if not isinstance(settings, dict):
            raise _BadRequest("settings must be an object of key: value")
        return {"ok": True, "gui": gui.set_gui(settings), "path": str(gui.gui_file())}

    def status(self) -> dict[str, Any]:
        conn = self._connect()
        try:
            by_state = {
                row["state"]: row["n"]
                for row in conn.execute("SELECT state, COUNT(*) AS n FROM files GROUP BY state")
            }
            chunks = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
            vectors = vec_count(conn) if db.has_vec_table(conn) else 0
            schema_version = db.get_schema_version(conn)
        finally:
            conn.close()
        files = sum(by_state.values())
        return {
            "ok": True,
            "files": files,
            "files_by_state": by_state,
            "chunks": chunks,
            "vectors": vectors,
            "vec_coverage": round(vectors / chunks, 4) if chunks else 0.0,
            "schema_version": schema_version,
            "embeddings": self.embedder is not None,
        }

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        op = request.get("op")
        try:
            if op == "ping":
                return self.ping()
            if op == "warmup":
                return self.warmup()
            if op == "launcher":
                return self.launcher(
                    str(request.get("query", "")),
                    limit=_as_int(request.get("limit"), "limit", minimum=1),
                    mode=_as_mode(request.get("mode")),
                    apps_limit=_as_count(
                        request.get("apps_limit"), "apps_limit", 8, minimum=0
                    ),
                )
            if op == "used":
                return self.used(
                    str(request.get("key", "")), _as_kind(request.get("kind"))
                )
            if op == "history":
                return self.history(request.get("add"))
            if op == "preview":
                return self.preview(
                    str(request.get("path", "")),
                    line=_as_int(request.get("line"), "line", minimum=1),
                    page=_as_int(request.get("page"), "page", minimum=1),
                    chunk_id=_as_int(request.get("chunk_id"), "chunk_id", minimum=1),
                )
            if op == "search":
                return self.search(
                    str(request.get("query", "")),
                    limit=_as_int(request.get("limit"), "limit", minimum=1),
                    mode=_as_mode(request.get("mode")),
                )
            if op == "embed":
                return self.embed(
                    _as_text_list(request.get("texts"), "texts"),
                    str(request.get("kind") or "doc"),
                    titles=_as_text_list(request.get("titles"), "titles") or None,
                )
            if op == "status":
                return self.status()
            if op == "get_gui":
                return self.get_gui()
            if op == "set_gui":
                return self.set_gui(request.get("settings"))
        except (_BadRequest, gui.GuiError) as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            log.exception("queryd operation failed: %s", op)
            return {"ok": False, "error": str(exc)}
        return {"ok": False, "error": f"unknown op: {op!r}"}


class _Handler(socketserver.StreamRequestHandler):
    #: A silent client gives the slot back after this many seconds (the clients
    #: reconnect: `QuerydClient` per request, the QML popup on disconnect).
    timeout = _CLIENT_TIMEOUT

    def handle(self) -> None:
        # A client that hangs up mid-exchange (or resets the connection) is
        # normal: without this, socketserver prints a traceback for every such
        # disconnect. Swallow it and let the thread end quietly.
        try:
            self._pump()
        except (BrokenPipeError, ConnectionResetError):
            log.debug("queryd: client disconnected mid-request")
        except TimeoutError:
            log.debug("queryd: client idle for %.0fs, closing the connection", _CLIENT_TIMEOUT)

    def _pump(self) -> None:
        while raw := self.rfile.readline(_MAX_REQUEST_BYTES):
            if not raw.endswith(b"\n"):
                # A request line that does not fit: stop reading instead of
                # letting the client grow the buffer without bound.
                self._reply({
                    "ok": False,
                    "error": f"request too large (max {_MAX_REQUEST_BYTES} bytes)",
                })
                return
            line = raw.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError as exc:
                response: dict[str, Any] = {"ok": False, "error": f"invalid JSON: {exc}"}
            else:
                response = cast("_UnixServer", self.server).engine.dispatch(request)
                # Echo the client's optional tag so it can route the reply.
                tag = request.get("req")
                if isinstance(tag, str):
                    response["req"] = tag
            self._reply(response)
            hook = getattr(self.server, "request_hook", None)
            if hook is not None:
                hook()

    def _reply(self, response: dict[str, Any]) -> None:
        self.wfile.write((json.dumps(response, ensure_ascii=False) + "\n").encode())
        self.wfile.flush()


class _UnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    #: Accepted-but-queued connections; the rest get a fast refusal (see below).
    request_queue_size = _MAX_CONNECTIONS

    def __init__(self, path: str, handler, engine: QueryEngine, request_hook=None, idle_hook=None):
        self.engine = engine
        self.request_hook = request_hook
        self.idle_hook = idle_hook
        self._slots = threading.BoundedSemaphore(_MAX_CONNECTIONS)
        super().__init__(path, handler)

    def process_request(self, request, client_address) -> None:
        """Serve at most ``_MAX_CONNECTIONS`` clients at once.

        Threads are daemon and unbounded by default, so a client that connects
        and then sits on it would pile up threads. Refusing right away (instead
        of blocking the accept loop) keeps the daemon answering the others.
        """
        if not self._slots.acquire(blocking=False):
            log.warning("queryd: refusing a connection: %d already active", _MAX_CONNECTIONS)
            with contextlib.suppress(OSError):
                request.sendall(b'{"ok":false,"error":"too many connections"}\n')
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class QuerydServer:
    """A query daemon bound to a Unix socket."""

    def __init__(
        self,
        engine: QueryEngine,
        *,
        socket_path: Path | None = None,
        request_hook: Callable[[], Any] | None = None,
        idle_hook: Callable[[], Any] | None = None,
    ) -> None:
        self.engine = engine
        self.socket_path = socket_path or paths.socket_path()
        self.request_hook = request_hook
        self.idle_hook = idle_hook
        self._server: _UnixServer | None = None
        self._thread: threading.Thread | None = None
        self._stop_idle = threading.Event()

    def _bind(self) -> _UnixServer:
        paths.ensure_runtime_dir()
        self.socket_path.unlink(missing_ok=True)
        server = _UnixServer(
            str(self.socket_path), _Handler, self.engine, self.request_hook, self.idle_hook
        )
        with contextlib.suppress(OSError):
            os.chmod(self.socket_path, 0o600)
        return server

    def start(self) -> QuerydServer:
        """Bind and serve in a background thread (used by tests and the CLI)."""
        self._server = self._bind()
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="feagle-queryd", daemon=True
        )
        self._thread.start()
        self._start_idle_loop()
        log.info("queryd listening on %s", self.socket_path)
        return self

    def serve_forever(self) -> None:
        """Blocking serve loop for the real daemon."""
        self._server = self._bind()
        self._start_idle_loop()
        log.info("queryd listening on %s", self.socket_path)
        try:
            self._server.serve_forever()
        finally:
            self.shutdown()

    def _start_idle_loop(self) -> None:
        hook = self.idle_hook
        if hook is None:
            return

        def loop() -> None:
            while not self._stop_idle.wait(30.0):
                with contextlib.suppress(Exception):
                    hook()

        threading.Thread(target=loop, name="feagle-idle", daemon=True).start()

    def shutdown(self) -> None:
        self._stop_idle.set()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        self.socket_path.unlink(missing_ok=True)


class QuerydClient:
    """Client for the query daemon protocol."""

    def __init__(self, socket_path: Path | None = None, *, timeout: float = 30.0) -> None:
        self.socket_path = socket_path or paths.socket_path()
        self.timeout = timeout

    def request(self, op: str, **fields: Any) -> dict[str, Any]:
        payload = json.dumps({"op": op, **fields}, ensure_ascii=False).encode() + b"\n"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(self.timeout)
            sock.connect(str(self.socket_path))
            sock.sendall(payload)
            buffer = b""
            while not buffer.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buffer += chunk
        return json.loads(buffer.decode("utf-8"))

    def is_running(self) -> bool:
        try:
            return bool(self.request("ping").get("ok"))
        except (OSError, ValueError):
            return False
