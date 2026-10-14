"""Tests for the indexer daemon loop."""

from __future__ import annotations

import threading

import pytest

from feagle import config, db
from feagle.indexer import pipeline
from feagle.indexer.daemon import IndexerDaemon
from feagle.indexer.watcher import Watcher


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


def test_producer_thread_is_daemon(root, conn):
    watcher = Watcher([root], source=lambda ev: iter(()))
    daemon = IndexerDaemon(
        make_cfg(root), conn=conn, watcher=watcher, pause_check=lambda: None
    )
    assert daemon.stop_event is not None
    assert isinstance(daemon.stop_event, threading.Event)
