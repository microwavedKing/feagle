"""Tests for the file watcher wrapper."""

from __future__ import annotations

import threading
from pathlib import Path

from watchfiles import Change

from feagle.indexer.watcher import Watcher, watched_paths


def test_watched_paths_extracts_paths():
    changes = {
        (Change.added, "/a/b.txt"),
        (Change.modified, "/a/c.txt"),
        (Change.deleted, "/a/d.txt"),
    }
    assert watched_paths(changes) == {Path("/a/b.txt"), Path("/a/c.txt"), Path("/a/d.txt")}


def test_events_use_injected_source(tmp_path):
    batches = [{tmp_path / "a.txt"}, {tmp_path / "b.txt", tmp_path / "c.txt"}]

    def source(stop_event):
        yield from batches

    watcher = Watcher([tmp_path], source=source)
    received = list(watcher.events(threading.Event()))
    assert received == batches


def test_roots_are_stored_as_paths(tmp_path):
    watcher = Watcher([str(tmp_path)], source=lambda ev: iter(()))
    assert watcher.roots == [tmp_path]
