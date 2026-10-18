"""Tests for the query daemon protocol."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import types
from pathlib import Path

import pytest

from feagle import config, db
from feagle.embeddings.client import ServerUnavailable
from feagle.search import queryd, vec
from feagle.search.queryd import QuerydClient, QuerydServer, QueryEngine


class FakeEmbedder:
    dim = 3

    def embed_queries(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]

    def embed_documents(self, items):
        return [[0.0, 1.0, 0.0] for _ in items]


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "index.sqlite"
    conn = db.open_database(path)
    db.ensure_vec_table(conn, 3)
    now = int(time.time())
    for i, (body, vector) in enumerate(
        [("alpha document", [1.0, 0.0, 0.0]), ("beta document", [0.0, 1.0, 0.0])]
    ):
        cur = conn.execute(
            "INSERT INTO files(path, size, mtime_ns, state, updated_at) VALUES(?,?,?,'ok',?)",
            (f"/d/{i}.txt", 10, 1, now),
        )
        fid = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO chunks(file_id, seq, line, body) VALUES(?,?,?,?)", (fid, 0, 1, body)
        )
        cid = cur.lastrowid
        conn.execute("INSERT INTO chunks_fts(body, chunk_id) VALUES(?, ?)", (body, cid))
        vec.insert_vectors(conn, [(cid, vector)])
    conn.commit()
    conn.close()
    return path


def make_cfg():
    cfg = config.default_config()
    cfg["search"]["limit"] = 5
    return cfg


def register_path(db_path, path: str, *, size: int = 0, mtime_ns: int = 0) -> None:
    """Insert an index row for ``path`` (which is what allows a preview of it)."""
    conn = db.open_database(db_path)
    try:
        conn.execute(
            "INSERT INTO files(path, size, mtime_ns, state, updated_at) VALUES(?,?,?,'ok',?)",
            (path, size, mtime_ns, int(time.time())),
        )
        conn.commit()
    finally:
        conn.close()


def register_file(db_path, target: Path) -> None:
    """Put a real file into the index, which is what allows a preview of it."""
    stat = target.stat()
    register_path(db_path, str(target), size=stat.st_size, mtime_ns=stat.st_mtime_ns)


@pytest.fixture
def engine(db_path):
    return QueryEngine(make_cfg(), db_path=db_path, embedder=FakeEmbedder())


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Never let a test write the real frecency/state file."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def test_dispatch_ping(engine):
    assert engine.dispatch({"op": "ping"})["ok"] is True


def test_dispatch_unknown_op(engine):
    result = engine.dispatch({"op": "nope"})
    assert result["ok"] is False
    assert "unknown op" in result["error"]


def test_search_hybrid(engine):
    result = engine.dispatch({"op": "search", "query": "alpha"})
    assert result["ok"] is True
    assert result["mode"] == "hybrid"
    assert result["results"][0]["path"] == "/d/0.txt"
    assert "took_ms" in result


def test_search_fts_only_when_no_embedder(db_path):
    engine = QueryEngine(make_cfg(), db_path=db_path, embedder=None)
    result = engine.dispatch({"op": "search", "query": "beta"})
    assert result["mode"] == "fts"
    assert result["results"][0]["path"] == "/d/1.txt"


def test_search_fts_mode_forces_text(db_path):
    engine = QueryEngine(make_cfg(), db_path=db_path, embedder=FakeEmbedder())
    result = engine.dispatch({"op": "search", "query": "alpha", "mode": "fts"})
    assert result["mode"] == "fts"


def test_embed_op(engine):
    result = engine.dispatch({"op": "embed", "texts": ["hello"], "kind": "query"})
    assert result == {"ok": True, "dim": 3, "embeddings": [[1.0, 0.0, 0.0]]}


def test_embed_op_passes_document_titles(db_path):
    seen: list[tuple[str, str | None]] = []

    class Recording(FakeEmbedder):
        def embed_documents(self, items):
            seen.extend(items)
            return super().embed_documents(items)

    engine = QueryEngine(make_cfg(), db_path=db_path, embedder=Recording())
    result = engine.dispatch(
        {"op": "embed", "texts": ["body a", "body b"], "titles": ["Title A"], "kind": "doc"}
    )
    assert result["ok"] is True
    assert seen == [("body a", "Title A"), ("body b", None)]


def test_embed_op_without_embedder(db_path):
    engine = QueryEngine(make_cfg(), db_path=db_path, embedder=None)
    result = engine.dispatch({"op": "embed", "texts": ["x"]})
    assert result["ok"] is False
    assert "hint" in result


def test_embedder_is_started_on_demand(db_path):
    calls = []

    def provider():
        calls.append(1)
        return FakeEmbedder()

    engine = QueryEngine(make_cfg(), db_path=db_path, embedder=None, embedder_provider=provider)
    result = engine.dispatch({"op": "search", "query": "alpha"})
    assert result["mode"] == "hybrid"
    assert calls == [1]

    # the adapter is cached: no further provider calls
    engine.dispatch({"op": "search", "query": "beta"})
    assert calls == [1]


def test_failed_provider_is_not_retried_immediately(db_path):
    clock = [100.0]
    calls = []

    def provider():
        calls.append(1)
        raise RuntimeError("no server")

    engine = QueryEngine(
        make_cfg(), db_path=db_path, embedder=None, embedder_provider=provider,
        clock=lambda: clock[0], retry_cooldown=30.0,
    )
    assert engine.dispatch({"op": "search", "query": "alpha"})["mode"] == "fts"
    assert engine.dispatch({"op": "search", "query": "alpha"})["mode"] == "fts"
    assert calls == [1]  # cooldown suppressed the second attempt

    clock[0] += 31
    engine.dispatch({"op": "search", "query": "alpha"})
    assert calls == [1, 1]  # retried after the cooldown


def test_embedding_failure_falls_back_and_resets(db_path):
    reset = []

    class Broken(FakeEmbedder):
        def embed_queries(self, texts):
            raise ServerUnavailable("gone")

    engine = QueryEngine(
        make_cfg(), db_path=db_path, embedder=Broken(),
        on_embedder_failure=lambda: reset.append(1),
    )
    result = engine.dispatch({"op": "search", "query": "alpha"})
    assert result["mode"] == "fts"  # degraded for this request
    assert reset == [1]
    assert engine.embedder is None  # next request may restart it


def test_embedding_failure_restarts_the_server_and_retries(db_path):
    """An unloaded server must come back on the same request, not 30 s later."""
    calls = []

    class Flaky(FakeEmbedder):
        def __init__(self):
            self.failures = 1

        def embed_queries(self, texts):
            if self.failures:
                self.failures -= 1
                raise ServerUnavailable("server unloaded")
            return super().embed_queries(texts)

    def provider():
        calls.append(1)
        return FakeEmbedder()

    engine = QueryEngine(
        make_cfg(), db_path=db_path, embedder=Flaky(), embedder_provider=provider,
        on_embedder_failure=lambda: None,
    )
    result = engine.dispatch({"op": "search", "query": "alpha"})
    assert result["mode"] == "hybrid"
    assert calls == [1]  # the provider was asked to start the server again


def test_retry_failure_still_degrades_to_text(db_path):
    def provider():
        raise ServerUnavailable("still down")

    class Broken(FakeEmbedder):
        def embed_queries(self, texts):
            raise ServerUnavailable("gone")

    engine = QueryEngine(
        make_cfg(), db_path=db_path, embedder=Broken(), embedder_provider=provider,
        on_embedder_failure=lambda: None,
    )
    assert engine.dispatch({"op": "search", "query": "alpha"})["mode"] == "fts"


def test_status_op(engine):
    result = engine.dispatch({"op": "status"})
    assert result["files"] == 2
    assert result["chunks"] == 2
    assert result["vectors"] == 2
    assert result["vec_coverage"] == 1.0


def test_error_is_reported_not_raised(db_path):
    engine = QueryEngine(make_cfg(), db_path=db_path)
    engine.db_path = db_path.parent / "missing.sqlite"
    result = engine.dispatch({"op": "status"})
    assert result["ok"] is False
    assert result["error"]


# -- server over a real Unix socket ---------------------------------------


@pytest.fixture
def server(engine, tmp_path):
    daemon = QuerydServer(engine, socket_path=tmp_path / "q.sock")
    daemon.start()
    yield daemon
    daemon.shutdown()


def test_socket_permissions(server, tmp_path):
    import stat

    mode = stat.S_IMODE((tmp_path / "q.sock").stat().st_mode)
    assert mode == 0o600


def test_client_ping(server, tmp_path):
    client = QuerydClient(tmp_path / "q.sock")
    assert client.request("ping")["version"]
    assert client.is_running() is True


def test_client_search(server, tmp_path):
    client = QuerydClient(tmp_path / "q.sock")
    result = client.request("search", query="alpha")
    assert result["ok"] is True
    assert result["results"][0]["path"] == "/d/0.txt"


def test_client_embed(server, tmp_path):
    client = QuerydClient(tmp_path / "q.sock")
    result = client.request("embed", texts=["hi"], kind="doc")
    assert result["dim"] == 3


def test_client_invalid_json(server, tmp_path):
    import socket

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.connect(str(tmp_path / "q.sock"))
        sock.sendall(b"not json\n")
        data = sock.recv(4096)
    assert json.loads(data)["ok"] is False


def test_is_running_false_when_absent(tmp_path):
    assert QuerydClient(tmp_path / "nope.sock").is_running() is False


@pytest.mark.parametrize("exc", [BrokenPipeError, ConnectionResetError])
def test_handler_ignores_a_client_that_hung_up(exc):
    """A dropped client must not leak an exception out of the handler."""

    class _Engine:
        def dispatch(self, request):
            return {"ok": True}

    class _DeadSocket:
        def write(self, data):
            raise exc("client gone")

        def flush(self):
            raise exc("client gone")

    handler = queryd._Handler.__new__(queryd._Handler)
    handler.rfile = [b'{"op":"ping"}\n']
    handler.wfile = _DeadSocket()
    handler.server = types.SimpleNamespace(engine=_Engine(), request_hook=None)
    handler.handle()  # must not raise


def test_client_disconnect_mid_reply_is_not_an_error(server, tmp_path, capsys, monkeypatch):
    """Aborting a connection while the daemon writes its reply is not an error."""
    import socket
    import struct

    original = server.engine.dispatch

    def slow_dispatch(request):
        time.sleep(0.2)
        return original(request)

    monkeypatch.setattr(server.engine, "dispatch", slow_dispatch)

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(str(tmp_path / "q.sock"))
    # SO_LINGER 0 → close() sends a RST, so the daemon's reply write fails.
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    sock.sendall(b'{"op":"ping"}\n')
    sock.close()

    time.sleep(0.5)  # let the daemon try (and fail) to write the reply

    assert "Traceback" not in capsys.readouterr().err
    # The daemon keeps serving other clients:
    assert QuerydClient(tmp_path / "q.sock").request("ping")["ok"] is True


# -- warmup / launcher -----------------------------------------------------


def test_warmup_starts_the_embedder_in_the_background(db_path):
    started = threading.Event()

    def provider():
        started.set()
        return FakeEmbedder()

    engine = QueryEngine(make_cfg(), db_path=db_path, embedder=None, embedder_provider=provider)
    assert engine.dispatch({"op": "warmup"})["ok"] is True
    assert started.wait(2.0)  # the provider ran off the request thread
    for _ in range(100):
        if engine.embedder is not None:
            break
        time.sleep(0.01)
    assert engine.embedder is not None


def test_warmup_without_provider(db_path):
    engine = QueryEngine(make_cfg(), db_path=db_path, embedder=None)
    result = engine.dispatch({"op": "warmup"})
    assert result["ok"] is False
    assert "hint" in result


def test_launcher_lists_apps_first_then_files(db_path, monkeypatch):
    from feagle import apps

    fake = [
        apps.App("fire.desktop", "Firefox", "firefox", False, Path("/x/fire.desktop"), "firefox")
    ]
    monkeypatch.setattr(apps, "discover_apps", lambda: fake)
    engine = QueryEngine(make_cfg(), db_path=db_path, embedder=FakeEmbedder())

    result = engine.dispatch({"op": "launcher", "query": "fire", "mode": "fts"})
    assert result["ok"] is True
    assert result["results"][0]["kind"] == "app"
    assert result["results"][0]["id"] == "fire.desktop"
    assert result["results"][0]["icon"] == "firefox"
    assert result["results"][0]["exec"]


def test_launcher_files_only_when_no_apps_match(db_path, monkeypatch):
    from feagle import apps

    monkeypatch.setattr(apps, "discover_apps", lambda: [])
    engine = QueryEngine(make_cfg(), db_path=db_path, embedder=None)

    result = engine.dispatch({"op": "launcher", "query": "alpha", "mode": "fts"})
    assert [r["kind"] for r in result["results"]] == ["file"]
    assert result["results"][0]["path"] == "/d/0.txt"


def test_launcher_app_cache_reuses_discovery(db_path, monkeypatch):
    from feagle import apps

    calls = []
    monkeypatch.setattr(apps, "discover_apps", lambda: calls.append(1) or [])
    engine = QueryEngine(make_cfg(), db_path=db_path, embedder=None)
    engine.dispatch({"op": "launcher", "query": "a", "mode": "fts"})
    engine.dispatch({"op": "launcher", "query": "b", "mode": "fts"})
    assert calls == [1]  # discovery cached in the engine


def _two_apps():
    from feagle import apps

    return [
        apps.App("alpha.desktop", "Alpha", "alpha", False, Path("/x/a.desktop")),
        apps.App("alps.desktop", "Alps", "alps", False, Path("/x/b.desktop")),
    ]


def test_used_op_records_a_launch(db_path, monkeypatch):
    from feagle import apps, frecency

    monkeypatch.setattr(apps, "discover_apps", lambda: [])
    store = frecency.FrecencyStore(db_path.parent / "frecency.json")
    engine = QueryEngine(make_cfg(), db_path=db_path, embedder=None, frecency_store=store)

    assert engine.dispatch({"op": "used", "key": "fire.desktop"})["ok"] is True
    assert store.scores()["fire.desktop"] == pytest.approx(1.0)


def test_launcher_boosts_frequently_used_apps(db_path, monkeypatch):
    from feagle import apps, frecency

    monkeypatch.setattr(apps, "discover_apps", _two_apps)
    store = frecency.FrecencyStore(db_path.parent / "frecency.json")
    for _ in range(int(frecency.SATURATION)):
        store.record("alps.desktop")
    engine = QueryEngine(make_cfg(), db_path=db_path, embedder=None, frecency_store=store)

    # Both match "al" as a prefix (the same tier); frecency decides the order.
    result = engine.dispatch({"op": "launcher", "query": "al", "mode": "fts"})
    assert [r["name"] for r in result["results"] if r["kind"] == "app"] == ["Alps", "Alpha"]


def test_launcher_browse_lists_used_apps_first(db_path, monkeypatch):
    from feagle import apps, frecency

    monkeypatch.setattr(apps, "discover_apps", _two_apps)
    store = frecency.FrecencyStore(db_path.parent / "frecency.json")
    store.record("alps.desktop")
    engine = QueryEngine(make_cfg(), db_path=db_path, embedder=None, frecency_store=store)

    result = engine.dispatch({"op": "launcher", "query": "", "mode": "fts"})
    assert [r["name"] for r in result["results"] if r["kind"] == "app"] == ["Alps", "Alpha"]


def test_launcher_frecency_never_beats_a_better_match(db_path, monkeypatch):
    from feagle import apps, frecency

    fake = [
        apps.App("al.desktop", "Al", "al", False, Path("/x/al.desktop")),          # exact
        apps.App("alps.desktop", "Alps", "alps", False, Path("/x/alps.desktop")),  # prefix
    ]
    monkeypatch.setattr(apps, "discover_apps", lambda: fake)
    store = frecency.FrecencyStore(db_path.parent / "frecency.json")
    for _ in range(50):
        store.record("alps.desktop")
    engine = QueryEngine(make_cfg(), db_path=db_path, embedder=None, frecency_store=store)

    result = engine.dispatch({"op": "launcher", "query": "al", "mode": "fts"})
    assert [r["name"] for r in result["results"] if r["kind"] == "app"] == ["Al", "Alps"]


def test_preview_text_around_the_matched_line(db_path, tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("\n".join(f"line {i}" for i in range(1, 51)), encoding="utf-8")
    register_file(db_path, target)
    engine = QueryEngine(make_cfg(), db_path=db_path, embedder=None)

    result = engine.dispatch({"op": "preview", "path": str(target), "line": 25})
    assert result["ok"] is True
    assert result["kind"] == "text"
    assert result["start_line"] == 13
    assert result["total_lines"] == 50
    lines = result["text"].splitlines()
    assert lines[0] == "line 13"
    assert lines[-1] == "line 37"


def test_preview_without_a_line_shows_the_top(db_path, tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("\n".join(f"line {i}" for i in range(1, 51)), encoding="utf-8")
    register_file(db_path, target)
    engine = QueryEngine(make_cfg(), db_path=db_path, embedder=None)

    result = engine.dispatch({"op": "preview", "path": str(target)})
    assert result["start_line"] == 1
    assert result["text"].splitlines()[-1] == "line 24"


def test_preview_normalises_the_path(db_path, tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("hello", encoding="utf-8")
    register_file(db_path, target)
    engine = QueryEngine(make_cfg(), db_path=db_path, embedder=None)

    # Same file, spelled with a redundant component the indexer never stored.
    result = engine.dispatch({"op": "preview", "path": str(tmp_path / "." / "note.txt")})
    assert result["ok"] is True
    assert result["kind"] == "text"


def test_preview_image_is_a_marker(db_path, tmp_path):
    target = tmp_path / "shot.png"
    target.write_bytes(b"\x89PNG\r\n\x1a\n")
    register_file(db_path, target)
    engine = QueryEngine(make_cfg(), db_path=db_path, embedder=None)

    result = engine.dispatch({"op": "preview", "path": str(target)})
    assert result["ok"] is True
    assert result["kind"] == "image"
    assert result["path"] == str(target)


def test_preview_binary_file_is_metadata(db_path, tmp_path):
    target = tmp_path / "blob.bin"
    target.write_bytes(b"\x00\x01\x02binary")
    register_file(db_path, target)
    engine = QueryEngine(make_cfg(), db_path=db_path, embedder=None)

    result = engine.dispatch({"op": "preview", "path": str(target)})
    assert result["ok"] is True
    assert result["kind"] == "meta"
    assert result["size"] == 9


def test_preview_outside_the_index_is_refused(db_path, tmp_path):
    """The socket must not become a way to read any file of the user."""
    target = tmp_path / "secret.txt"
    target.write_text("not indexed", encoding="utf-8")
    engine = QueryEngine(make_cfg(), db_path=db_path, embedder=None)

    result = engine.dispatch({"op": "preview", "path": str(target)})
    assert result["ok"] is False
    assert "not in the index" in result["error"]


def test_preview_of_a_sibling_file_is_refused(db_path, tmp_path):
    """A sibling of an indexed file is still not indexed."""
    indexed = tmp_path / "note.txt"
    indexed.write_text("line 1\n", encoding="utf-8")
    register_file(db_path, indexed)
    sibling = tmp_path / "other.txt"
    sibling.write_text("line 1\n", encoding="utf-8")
    engine = QueryEngine(make_cfg(), db_path=db_path, embedder=None)

    assert engine.dispatch({"op": "preview", "path": str(indexed)})["ok"] is True
    assert engine.dispatch({"op": "preview", "path": str(sibling)})["ok"] is False


def test_preview_refuses_a_symlink(db_path, tmp_path):
    real = tmp_path / "real.txt"
    real.write_text("payload\n", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(real)
    register_file(db_path, link)  # even a row for the link does not unlock it
    engine = QueryEngine(make_cfg(), db_path=db_path, embedder=None)

    result = engine.dispatch({"op": "preview", "path": str(link)})
    assert result["ok"] is False
    assert "not in the index" in result["error"]


def test_preview_allows_a_symlink_when_follow_symlinks_is_on(db_path, tmp_path):
    real = tmp_path / "real.txt"
    real.write_text("payload\n", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(real)
    register_file(db_path, link)
    cfg = make_cfg()
    cfg["index"]["follow_symlinks"] = True
    engine = QueryEngine(cfg, db_path=db_path, embedder=None)

    result = engine.dispatch({"op": "preview", "path": str(link)})
    assert result["ok"] is True
    assert "payload" in result["text"]


def test_preview_without_a_database_is_refused(db_path, tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("hello", encoding="utf-8")
    engine = QueryEngine(make_cfg(), db_path=db_path, embedder=None)
    engine.db_path = db_path.parent / "missing.sqlite"

    result = engine.dispatch({"op": "preview", "path": str(target)})
    assert result["ok"] is False
    assert "not in the index" in result["error"]


def test_preview_with_a_schemaless_database_is_refused(tmp_path):
    """A database without the `files` table must not crash the daemon."""
    empty = tmp_path / "empty.sqlite"
    sqlite3.connect(empty).close()
    target = tmp_path / "note.txt"
    target.write_text("hello", encoding="utf-8")
    engine = QueryEngine(make_cfg(), db_path=empty, embedder=None)

    result = engine.dispatch({"op": "preview", "path": str(target)})
    assert result["ok"] is False
    assert "not in the index" in result["error"]


def test_preview_missing_file_is_an_error(db_path, tmp_path):
    engine = QueryEngine(make_cfg(), db_path=db_path, embedder=None)
    result = engine.dispatch({"op": "preview", "path": str(tmp_path / "gone.txt")})
    assert result["ok"] is False
    assert "not in the index" in result["error"]


def test_preview_of_an_indexed_file_that_vanished_is_an_error(db_path, tmp_path):
    target = tmp_path / "gone.txt"
    target.write_text("bye\n", encoding="utf-8")
    register_file(db_path, target)
    target.unlink()
    engine = QueryEngine(make_cfg(), db_path=db_path, embedder=None)

    result = engine.dispatch({"op": "preview", "path": str(target)})
    assert result["ok"] is False
    assert "cannot read" in result["error"]


def test_preview_directory_is_an_error(db_path, tmp_path):
    register_path(db_path, str(tmp_path))
    engine = QueryEngine(make_cfg(), db_path=db_path, embedder=None)
    result = engine.dispatch({"op": "preview", "path": str(tmp_path)})
    assert result["ok"] is False
    assert "not a regular file" in result["error"]


# ---- PDF page previews ---------------------------------------------------


TWO_PAGES = Path(__file__).parent / "fixtures" / "two_pages.pdf"


@pytest.fixture
def pdf_chunk(db_path):
    """A chunk whose body matches page 2 of the PDF fixture."""
    conn = db.open_database(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO files(path, size, mtime_ns, state, updated_at) VALUES(?,?,?,'ok',?)",
            (str(TWO_PAGES), 100, 1, 0),
        )
        file_id = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO chunks(file_id, seq, page, line, time_ms, body) VALUES(?,?,?,?,?,?)",
            (file_id, 0, 2, None, None,
             "Page two heading\nEcho foxtrot golf hotel in the second page."),
        )
        chunk_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return chunk_id


def _needs_poppler():
    import shutil

    if shutil.which("pdftotext") is None:
        pytest.skip("poppler (pdftotext) is required")


def test_find_anchor_matches_the_first_non_empty_line():
    lines = ["", "Page two heading", "Echo foxtrot"]
    assert queryd._find_anchor(lines, "\n\nPage two heading\nEcho foxtrot") == 2
    assert queryd._find_anchor(lines, "   ") is None
    assert queryd._find_anchor(lines, "nothing like this") is None


def test_preview_pdf_page_anchors_on_the_chunk(db_path, pdf_chunk):
    _needs_poppler()
    engine = QueryEngine(make_cfg(), db_path=db_path, embedder=None)

    result = engine.dispatch({
        "op": "preview", "path": str(TWO_PAGES), "page": 2, "chunk_id": pdf_chunk,
    })
    assert result["ok"] is True
    assert result["kind"] == "text"
    assert result["page"] == 2
    assert result["line"] == 1
    assert "Echo foxtrot golf hotel" in result["text"]


def test_preview_pdf_page_without_a_chunk_starts_at_the_top(db_path, pdf_chunk):
    _needs_poppler()
    engine = QueryEngine(make_cfg(), db_path=db_path, embedder=None)

    result = engine.dispatch({"op": "preview", "path": str(TWO_PAGES), "page": 1})
    assert result["kind"] == "text"
    assert result["line"] is None
    assert result["start_line"] == 1
    assert "Alpha bravo charlie delta" in result["text"]


def test_preview_pdf_without_poppler_degrades_to_metadata(db_path, monkeypatch):
    monkeypatch.setattr(queryd.shutil, "which", lambda name: None)
    register_file(db_path, TWO_PAGES)
    engine = QueryEngine(make_cfg(), db_path=db_path, embedder=None)

    result = engine.dispatch({"op": "preview", "path": str(TWO_PAGES), "page": 1})
    assert result["ok"] is True
    assert result["kind"] == "meta"
    assert "poppler" in result["reason"]


def test_preview_pdf_page_is_rendered_once(db_path, pdf_chunk, monkeypatch):
    _needs_poppler()
    engine = QueryEngine(make_cfg(), db_path=db_path, embedder=None)
    calls = []
    real_run = queryd.subprocess.run

    def counting_run(command, **kwargs):
        calls.append(command)
        return real_run(command, **kwargs)

    monkeypatch.setattr(queryd.subprocess, "run", counting_run)
    for _ in range(3):
        engine.dispatch({"op": "preview", "path": str(TWO_PAGES), "page": 2, "chunk_id": pdf_chunk})
    assert len(calls) == 1
