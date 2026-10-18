"""Tests for the indexer daemon loop."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from feagle import config, db
from feagle.embeddings.client import ServerUnavailable
from feagle.indexer import daemon as daemon_module
from feagle.indexer import pipeline
from feagle.indexer.daemon import IndexerDaemon, _ResilientEmbedder
from feagle.indexer.watcher import Watcher


class FakeEmbedder:
    dim = 4

    def embed_documents(self, items):
        return [[1.0] * self.dim for _ in items]

    def embed_queries(self, texts):
        return [[0.0] * self.dim for _ in texts]


def embedded_of(conn, path):
    return conn.execute(
        "SELECT embedded FROM files WHERE path = ?", (os.path.realpath(path),)
    ).fetchone()["embedded"]


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "root"
    path.mkdir()
    (path / "a.txt").write_text("hello", encoding="utf-8")
    return path


@pytest.fixture
def conn(tmp_path):
    connection = db.open_database(tmp_path / "index.sqlite")
    yield connection
    connection.close()


def make_cfg(root):
    cfg = config.default_config()
    cfg["index"]["roots"] = [str(root)]
    return cfg


def test_roots_for_selects_matching_roots(root):
    other = root.parent / "other"
    other.mkdir()
    cfg = make_cfg(root)
    cfg["index"]["roots"] = [str(root), str(other)]
    daemon = IndexerDaemon(cfg, pause_check=lambda: None)

    assert daemon.roots_for([root / "deep" / "x.txt"]) == [str(root.resolve())]
    assert daemon.roots_for([other / "y.txt", root / "z.txt"]) == sorted(
        [str(root.resolve()), str(other.resolve())]
    )
    assert daemon.roots_for([root.parent / "elsewhere.txt"]) is None


def test_run_once_indexes(root, conn):
    calls = []
    daemon = IndexerDaemon(
        make_cfg(root),
        conn=conn,
        embedder_provider=lambda: calls.append(1) or None,
        pause_check=lambda: None,
        rescan_interval_s=0,
    )
    stats = daemon.run_once()
    assert stats.reindexed == 1
    assert calls == [1]


def test_run_once_respects_pause(root, conn):
    calls = []
    daemon = IndexerDaemon(
        make_cfg(root),
        conn=conn,
        embedder_provider=lambda: calls.append(1) or None,
        pause_check=lambda: "on battery",
        rescan_interval_s=0,
    )
    stats = daemon.run_once()
    assert stats.paused == "on battery"
    assert stats.reindexed == 0
    assert calls == []  # embedder never requested


def test_run_without_roots_returns():
    cfg = config.default_config()
    daemon = IndexerDaemon(cfg, pause_check=lambda: None)
    daemon.run()  # must not raise or block


def test_run_processes_watch_batches(root, conn):
    passes = []

    def source(stop_event):
        yield {root / "a.txt"}
        yield {root / "a.txt", root / "b.txt"}

    watcher = Watcher([root], source=source)
    daemon = IndexerDaemon(
        make_cfg(root),
        conn=conn,
        embedder_provider=lambda: passes.append("pass") or None,
        watcher=watcher,
        pause_check=lambda: None,
        rescan_interval_s=0,
    )
    daemon.run()
    # one initial pass plus one per change batch
    assert len(passes) == 3


def test_ignores_changes_outside_roots(root, conn, tmp_path):
    passes = []

    def source(stop_event):
        yield {tmp_path / "outside.txt"}

    daemon = IndexerDaemon(
        make_cfg(root),
        conn=conn,
        embedder_provider=lambda: passes.append("pass") or None,
        watcher=Watcher([root], source=source),
        pause_check=lambda: None,
        rescan_interval_s=0,
    )
    daemon.run()
    assert len(passes) == 1  # only the initial pass


def test_stop_event_ends_loop(root, conn):
    def source(stop_event):
        stop_event.set()
        yield {root / "a.txt"}

    daemon = IndexerDaemon(
        make_cfg(root),
        conn=conn,
        watcher=Watcher([root], source=source),
        pause_check=lambda: None,
        rescan_interval_s=0,
        embedder_provider=lambda: None,
    )
    daemon.run()  # returns promptly


def test_paused_rescan_is_scheduled_sooner(root, conn):
    clock = [100.0]
    daemon = IndexerDaemon(
        make_cfg(root),
        conn=conn,
        pause_check=lambda: "on battery",
        clock=lambda: clock[0],
        rescan_interval_s=3600,
    )
    stats = pipeline.IndexStats(paused="on battery")
    assert daemon._next_rescan(stats) == 100.0 + 300.0
    assert daemon._next_rescan(pipeline.IndexStats()) == 100.0 + 3600


def test_a_paused_pass_is_retried_even_without_a_rescan_interval(root, conn, monkeypatch):
    """Disabling periodic rescans must not turn a pause into an endless stall."""
    monkeypatch.setattr(daemon_module, "PAUSED_RETRY_S", 0.05)
    reasons = ["busy"]

    def pause_check():
        return reasons.pop(0) if reasons else None

    def source(stop_event):
        time.sleep(0.2)
        yield None  # end the watcher, so the loop can leave

    indexer = IndexerDaemon(
        make_cfg(root),
        conn=conn,
        watcher=Watcher([root], source=source),
        pause_check=pause_check,
        rescan_interval_s=0,
    )
    indexer.run()

    # the initial pass was paused, the retry indexed the file
    assert conn.execute("SELECT COUNT(*) AS n FROM files").fetchone()["n"] == 1


def test_no_rescan_is_scheduled_when_nothing_is_configured(root, conn):
    indexer = IndexerDaemon(
        make_cfg(root), conn=conn, pause_check=lambda: None, rescan_interval_s=0
    )
    assert indexer._next_rescan(pipeline.IndexStats()) is None
    assert indexer._next_rescan(pipeline.IndexStats(paused="busy")) is not None


def test_producer_thread_is_daemon(root, conn):
    watcher = Watcher([root], source=lambda ev: iter(()))
    daemon = IndexerDaemon(
        make_cfg(root), conn=conn, watcher=watcher, pause_check=lambda: None
    )
    assert daemon.stop_event is not None
    assert isinstance(daemon.stop_event, threading.Event)


# -- embedder reconnect / backfill ----------------------------------------


def test_index_once_backfills_files_that_still_lack_vectors(root, conn):
    (root / "a.txt").write_text("content", encoding="utf-8")
    cfg = make_cfg(root)
    pipeline.index_roots(conn, cfg)  # no embedder -> embedded=0
    assert embedded_of(conn, root / "a.txt") == 0

    daemon = IndexerDaemon(
        cfg, conn=conn, embedder_provider=lambda: FakeEmbedder(), pause_check=lambda: None
    )
    stats = daemon.index_once()
    assert stats.backfilled == 1
    assert embedded_of(conn, root / "a.txt") == 1


def test_embedder_provider_is_retried_after_a_startup_failure(root, conn):
    (root / "a.txt").write_text("content", encoding="utf-8")
    cfg = make_cfg(root)
    attempts = []

    def provider():
        attempts.append(1)
        if len(attempts) == 1:
            raise ServerUnavailable("server down")
        return FakeEmbedder()

    daemon = IndexerDaemon(cfg, conn=conn, embedder_provider=provider, pause_check=lambda: None)
    first = daemon.run_once()
    assert first.vectors == 0
    assert embedded_of(conn, root / "a.txt") == 0
    assert len(attempts) == 1

    # The next pass asks for the embedder again and backfills the missing vectors.
    second = daemon.run_once()
    assert second.backfilled == 1
    assert embedded_of(conn, root / "a.txt") == 1
    assert len(attempts) == 2


def test_embedder_restarts_when_the_server_drops(root, conn):
    (root / "a.txt").write_text("content", encoding="utf-8")

    class Flaky:
        dim = 4

        def embed_documents(self, items):
            raise ServerUnavailable("server unloaded")

        def embed_queries(self, texts):
            raise ServerUnavailable("server unloaded")

    attempts = []
    reset = []

    def provider():
        attempts.append(1)
        return Flaky() if len(attempts) == 1 else FakeEmbedder()

    daemon = IndexerDaemon(
        make_cfg(root),
        conn=conn,
        embedder_provider=provider,
        on_embedder_failure=lambda: reset.append(1),
        pause_check=lambda: None,
    )
    stats = daemon.index_once()
    assert stats.vectors == 1
    assert embedded_of(conn, root / "a.txt") == 1
    assert reset == [1]
    assert len(attempts) == 2


def test_embedder_restart_failure_degrades_instead_of_crashing(root, conn):
    (root / "a.txt").write_text("content", encoding="utf-8")

    class Flaky:
        dim = 4

        def embed_documents(self, items):
            raise ServerUnavailable("down")

        def embed_queries(self, texts):
            raise ServerUnavailable("down")

    calls = []

    def provider():
        calls.append(1)
        if len(calls) == 1:
            return Flaky()
        raise OSError("model vanished")

    daemon = IndexerDaemon(
        make_cfg(root),
        conn=conn,
        embedder_provider=provider,
        on_embedder_failure=lambda: None,
        pause_check=lambda: None,
    )
    stats = daemon.index_once()  # must not raise
    assert stats.vectors == 0
    assert embedded_of(conn, root / "a.txt") == 0


def _wrap(embedder):
    return _ResilientEmbedder(embedder, provider=lambda: embedder, on_failure=lambda: None)


def test_resilient_embedder_does_not_hide_embed_images():
    """Image search in the daemon: the wrapper must forward ``embed_images``."""

    class WithImages:
        dim = 3

        def embed_documents(self, items):
            return [[0.0]]

        def embed_queries(self, texts):
            return [[0.0]]

        def embed_images(self, paths):
            return [[1.0, 2.0, 3.0]]

    class TextOnly:
        dim = 3

        def embed_documents(self, items):
            return [[0.0]]

        def embed_queries(self, texts):
            return [[0.0]]

    multimodal = _wrap(WithImages())
    assert getattr(multimodal, "embed_images", None) is not None
    assert multimodal.embed_images([Path("x.png")]) == [[1.0, 2.0, 3.0]]

    # A text-only embedder must still look incapable, so the pipeline can skip
    # images with a clear message instead of crashing.
    assert getattr(_wrap(TextOnly()), "embed_images", None) is None


def test_resilient_embedder_retries_an_image_batch_after_a_drop():
    class Flaky:
        calls = 0

        dim = 3

        def embed_documents(self, items):
            return [[0.0]]

        def embed_queries(self, texts):
            return [[0.0]]

        def embed_images(self, paths):
            Flaky.calls += 1
            if Flaky.calls == 1:
                raise ServerUnavailable("server unloaded")
            return [[9.0, 9.0, 9.0]]

    resilient = _wrap(Flaky())
    assert resilient.embed_images([Path("x.png")]) == [[9.0, 9.0, 9.0]]
    assert Flaky.calls == 2


def test_idle_hook_is_called_in_the_wait_loop(root, conn):
    calls = []

    def source(stop_event):
        yield {root / "a.txt"}

    daemon = IndexerDaemon(
        make_cfg(root),
        conn=conn,
        watcher=Watcher([root], source=source),
        pause_check=lambda: None,
        rescan_interval_s=0,
        idle_hook=lambda: calls.append(1),
    )
    daemon.run()
    assert calls  # the hook ran while waiting for events


def test_idle_hook_failure_does_not_stop_the_daemon(root, conn):
    def source(stop_event):
        yield {root / "a.txt"}

    def boom():
        raise RuntimeError("bad hook")

    daemon = IndexerDaemon(
        make_cfg(root),
        conn=conn,
        watcher=Watcher([root], source=source),
        pause_check=lambda: None,
        rescan_interval_s=0,
        idle_hook=boom,
    )
    daemon.run()  # must not raise


# ---- targeted re-indexing and config hot-reload -------------------------


def test_watch_batch_does_not_rescan_the_whole_root(root, conn, monkeypatch):
    (root / "b.txt").write_text("second", encoding="utf-8")
    walks = []
    original = pipeline.index_roots

    def counting(conn_, cfg_, **kwargs):
        walks.append(kwargs.get("roots"))
        return original(conn_, cfg_, **kwargs)

    monkeypatch.setattr(pipeline, "index_roots", counting)

    def source(stop_event):
        yield {root / "b.txt"}

    daemon = IndexerDaemon(
        make_cfg(root), conn=conn, embedder_provider=lambda: None,
        watcher=Watcher([root], source=source), pause_check=lambda: None, rescan_interval_s=0,
    )
    daemon.run()
    assert len(walks) == 1  # the initial pass only; the batch was targeted


def test_watch_batch_prunes_a_deleted_file(root, conn):
    target = root / "a.txt"

    def source(stop_event):
        target.unlink()  # the watcher reports the deletion
        yield {target}

    daemon = IndexerDaemon(
        make_cfg(root), conn=conn, embedder_provider=lambda: None,
        watcher=Watcher([root], source=source), pause_check=lambda: None, rescan_interval_s=0,
    )
    daemon.run()
    assert conn.execute("SELECT COUNT(*) AS n FROM files").fetchone()["n"] == 0


def test_config_reload_keeps_the_old_config_on_error(root, conn):
    def broken():
        raise ValueError("invalid TOML")

    daemon = IndexerDaemon(
        make_cfg(root), conn=conn, pause_check=lambda: None, watch=False, reload_config=broken
    )
    assert daemon._reload_config() is False
    assert daemon.cfg["index"]["roots"] == [str(root)]


def test_config_reload_reports_root_changes(root, conn, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    fresh = make_cfg(other)
    daemon = IndexerDaemon(
        make_cfg(root), conn=conn, pause_check=lambda: None, watch=False,
        reload_config=lambda: fresh,
    )
    assert daemon._reload_config() is True   # the roots changed
    assert daemon._reload_config() is False  # the same config again
    assert daemon.cfg["index"]["roots"] == [str(other)]


def test_config_reload_applies_settings_and_rescan_interval(root, conn):
    fresh = make_cfg(root)
    fresh["index"]["max_chunks_per_file"] = 1
    fresh["index"]["rescan_interval_min"] = 5
    daemon = IndexerDaemon(
        make_cfg(root), conn=conn, pause_check=lambda: None, watch=False,
        reload_config=lambda: fresh,
    )
    assert daemon._reload_config() is False  # same roots: no watcher restart
    assert daemon.cfg["index"]["max_chunks_per_file"] == 1
    assert daemon.rescan_interval_s == 300.0


def test_config_reload_does_not_override_an_injected_pause_check(root, conn):
    injected = lambda: "always"  # noqa: E731
    fresh = make_cfg(root)
    daemon = IndexerDaemon(
        make_cfg(root), conn=conn, pause_check=injected, watch=False, reload_config=lambda: fresh
    )
    daemon._reload_config()
    assert daemon.pause_check is injected


def test_roots_change_restarts_the_watcher(root, conn, tmp_path):
    import queue

    other = tmp_path / "other"
    other.mkdir()
    daemon = IndexerDaemon(
        make_cfg(root), conn=conn, pause_check=lambda: None, watch=False,
        reload_config=lambda: make_cfg(other),
    )
    events: queue.Queue = queue.Queue()
    daemon.watcher = Watcher([root], source=lambda stop: iter(()))
    daemon._start_watcher(events)
    first = daemon._watcher_generation

    assert daemon._reload_config() is True
    daemon._restart_watcher(events)
    assert daemon._watcher_generation == first + 1
    assert daemon.watcher.roots == [other]


def test_roots_change_reconciles_the_index(root, conn, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "secret.txt").write_text("secret", encoding="utf-8")

    two_roots = make_cfg(root)
    two_roots["index"]["roots"] = [str(root), str(other)]
    pipeline.index_roots(conn, two_roots)
    assert conn.execute("SELECT COUNT(*) AS n FROM files").fetchone()["n"] == 2

    daemon = IndexerDaemon(
        two_roots, conn=conn, embedder_provider=lambda: None, pause_check=lambda: None,
        watch=False, reload_config=lambda: make_cfg(root),
    )
    assert daemon._reload_config() is True
    daemon._apply_root_change(None)  # what the loop does on a root change

    paths = [row["path"] for row in conn.execute("SELECT path FROM files")]
    assert paths == [os.path.realpath(root / "a.txt")]


def test_run_applies_reloaded_settings_without_a_restart(root, conn):
    fresh = make_cfg(root)
    fresh["index"]["max_chunks_per_file"] = 1
    seen = []

    def source(stop_event):
        yield {root / "a.txt"}

    daemon = IndexerDaemon(
        make_cfg(root), conn=conn, embedder_provider=lambda: None,
        watcher=Watcher([root], source=source), pause_check=lambda: None, rescan_interval_s=0,
        reload_config=lambda: (seen.append(1), fresh)[1],
    )
    daemon.run()
    assert seen  # the reload was consulted between iterations
    assert daemon.cfg["index"]["max_chunks_per_file"] == 1
    assert daemon._watcher_generation == 1  # roots unchanged: no restart
