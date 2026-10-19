"""Tests for the query history store."""

from __future__ import annotations

import json
import logging
import stat
from typing import TYPE_CHECKING

from feagle import history

if TYPE_CHECKING:
    from pathlib import Path


def _store(tmp_path: Path) -> history.QueryHistory:
    return history.QueryHistory(tmp_path / "history.json")


def test_empty_when_missing(tmp_path):
    assert _store(tmp_path).entries() == []


def test_add_puts_the_newest_first_and_dedupes(tmp_path):
    store = _store(tmp_path)
    store.add("alpha")
    store.add("beta")
    assert store.add("alpha") == ["alpha", "beta"]


def test_add_strips_and_ignores_empty(tmp_path):
    store = _store(tmp_path)
    assert store.add("   ") == []
    assert store.add("  spaced  ") == ["spaced"]


def test_add_ignores_over_long_queries(tmp_path):
    store = _store(tmp_path)
    store.add("ok")
    assert store.add("x" * (history.MAX_CHARS + 1)) == ["ok"]


def test_caps_the_list(tmp_path):
    store = _store(tmp_path)
    for index in range(history.MAX_ENTRIES + 5):
        store.add(f"q{index}")
    entries = store.entries(history.MAX_ENTRIES)
    assert len(entries) == history.MAX_ENTRIES
    assert entries[0] == f"q{history.MAX_ENTRIES + 4}"
    assert entries[-1] == "q5"


def test_round_trip_through_the_file(tmp_path):
    path = tmp_path / "history.json"
    history.QueryHistory(path).add("rust ownership")
    assert json.loads(path.read_text(encoding="utf-8")) == ["rust ownership"]
    assert history.QueryHistory(path).entries() == ["rust ownership"]


def test_file_is_private(tmp_path):
    path = tmp_path / "history.json"
    history.QueryHistory(path).add("q")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_corrupt_file_means_no_history(tmp_path, caplog):
    path = tmp_path / "history.json"
    path.write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="feagle.history"):
        assert history.QueryHistory(path).entries() == []
    assert caplog.records


def test_a_foreign_change_is_reloaded(tmp_path):
    path = tmp_path / "history.json"
    store = history.QueryHistory(path)
    store.add("one")
    # Another process (or the CLI) rewrites the file.
    path.write_text(json.dumps(["two", "one"]), encoding="utf-8")
    assert store.entries() == ["two", "one"]


def test_junk_entries_are_dropped(tmp_path):
    path = tmp_path / "history.json"
    path.write_text(json.dumps([1, "", "  ", "ok", None]), encoding="utf-8")
    assert history.QueryHistory(path).entries() == ["ok"]
