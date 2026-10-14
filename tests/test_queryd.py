"""Tests for the query daemon protocol."""

from __future__ import annotations

import json
import time

import pytest

from feagle import config, db
from feagle.embeddings.client import ServerUnavailable
from feagle.search import vec
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


@pytest.fixture
def engine(db_path):
    return QueryEngine(make_cfg(), db_path=db_path, embedder=FakeEmbedder())


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
