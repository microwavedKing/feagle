"""The query daemon: a line-delimited JSON protocol over a Unix socket.

One request per line, one response per line. The socket lives in
``$XDG_RUNTIME_DIR/feagle/`` (0700) and is created 0600. Each connection is
handled on its own thread and opens its own read-only database connection, so
no SQLite object is shared across threads.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import socket
import socketserver
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import __version__, db, frecency, paths
from ..embeddings.client import EmbeddingError, ServerUnavailable
from ..log import get_logger
from ..search import hybrid
from ..search.vec import vec_count

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

        files = self.search(query, limit=limit, mode=mode)
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
        return {
            "ok": True,
            "mode": files["mode"],
            "took_ms": files["took_ms"],
            "results": results,
        }

    def used(self, key: str) -> dict[str, Any]:
        """Remember that ``key`` was launched (feeds the frecency ranking)."""
        self._frecency.record(key)
        return {"ok": True}

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
        """
        from ..indexer import extract

        target = Path(path)
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

    def search(
        self, query: str, *, limit: int | None = None, mode: str | None = None
    ) -> dict[str, Any]:
        search_cfg = self.cfg["search"]
        limit = limit or search_cfg["limit"]
        mode = mode or search_cfg["mode"]
        stem = search_cfg["stemming"] != "none"
        started = time.perf_counter()

        conn = self._connect()
        try:
            embedder = self._ensure_embedder()
            use_hybrid = (
                mode in ("auto", "hybrid")
                and embedder is not None
                and db.has_vec_table(conn)
            )
            if use_hybrid:
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
                            embedder = None
                    if embedder is None:
                        use_hybrid = False
            if use_hybrid:
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
                    limit=request.get("limit"),
                    mode=request.get("mode"),
                    apps_limit=int(request.get("apps_limit", 8)),
                )
            if op == "used":
                return self.used(str(request.get("key", "")))
            if op == "preview":
                line = request.get("line")
                page = request.get("page")
                chunk_id = request.get("chunk_id")
                return self.preview(
                    str(request.get("path", "")),
                    line=int(line) if line is not None else None,
                    page=int(page) if page is not None else None,
                    chunk_id=int(chunk_id) if chunk_id is not None else None,
                )
            if op == "search":
                return self.search(
                    str(request.get("query", "")),
                    limit=request.get("limit"),
                    mode=request.get("mode"),
                )
            if op == "embed":
                texts = request.get("texts") or []
                return self.embed(
                    list(texts),
                    str(request.get("kind", "doc")),
                    titles=request.get("titles"),
                )
            if op == "status":
                return self.status()
        except Exception as exc:  # noqa: BLE001 - the protocol must never crash
            log.exception("queryd operation failed: %s", op)
            return {"ok": False, "error": str(exc)}
        return {"ok": False, "error": f"unknown op: {op!r}"}


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        for raw in self.rfile:
            line = raw.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError as exc:
                response: dict[str, Any] = {"ok": False, "error": f"invalid JSON: {exc}"}
            else:
                response = self.server.engine.dispatch(request)
                # Echo the client's optional tag so it can route the reply.
                tag = request.get("req")
                if isinstance(tag, str):
                    response["req"] = tag
            self.wfile.write((json.dumps(response, ensure_ascii=False) + "\n").encode())
            self.wfile.flush()
            hook = getattr(self.server, "request_hook", None)
            if hook is not None:
                hook()


class _UnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, path: str, handler, engine: QueryEngine, request_hook=None, idle_hook=None):
        self.engine = engine
        self.request_hook = request_hook
        self.idle_hook = idle_hook
        super().__init__(path, handler)


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
        if self.idle_hook is None:
            return

        def loop() -> None:
            while not self._stop_idle.wait(30.0):
                with contextlib.suppress(Exception):
                    self.idle_hook()

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
