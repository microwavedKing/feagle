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
import socket
import socketserver
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import __version__, db, paths
from ..log import get_logger
from ..search import hybrid
from ..search.vec import vec_count

log = get_logger("search.queryd")

_EMBEDDINGS_HINT = "start llama-server and run 'feagle setup' to enable semantic search"


class QueryEngine:
    """Executes protocol operations against the index."""

    def __init__(
        self,
        cfg: dict[str, Any],
        *,
        db_path: Path | None = None,
        embedder: Any | None = None,
    ) -> None:
        self.cfg = cfg
        self.db_path = db_path or paths.db_path()
        self.embedder = embedder

    def _connect(self):
        return db.connect(self.db_path, read_only=True)

    # -- operations --------------------------------------------------------

    def ping(self) -> dict[str, Any]:
        return {"ok": True, "version": __version__}

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
            use_hybrid = (
                mode in ("auto", "hybrid")
                and self.embedder is not None
                and db.has_vec_table(conn)
            )
            if use_hybrid:
                vector = self.embedder.embed_queries([query])[0]
                hits = hybrid.search_hybrid(
                    conn,
                    query,
                    query_vector=vector,
                    limit=limit,
                    candidates=search_cfg["candidates"],
                    rrf_k=search_cfg["rrf_k"],
                    stem=stem,
                    max_distance=search_cfg["max_distance"],
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
                    "snippet": hit.snippet,
                    "chunk_id": hit.chunk_id,
                }
                for rank, hit in enumerate(hits, start=1)
            ],
        }

    def embed(self, texts: list[str], kind: str = "doc") -> dict[str, Any]:
        if self.embedder is None:
            return {"ok": False, "error": "embeddings unavailable", "hint": _EMBEDDINGS_HINT}
        if kind == "query":
            vectors = self.embedder.embed_queries(texts)
        else:
            vectors = self.embedder.embed_documents([(text, None) for text in texts])
        return {"ok": True, "dim": self.embedder.dim, "embeddings": vectors}

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
            if op == "search":
                return self.search(
                    str(request.get("query", "")),
                    limit=request.get("limit"),
                    mode=request.get("mode"),
                )
            if op == "embed":
                texts = request.get("texts") or []
                return self.embed(list(texts), str(request.get("kind", "doc")))
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
