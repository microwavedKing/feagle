"""Tests for the combined application/file picker."""

from __future__ import annotations

from pathlib import Path

import pytest

from feagle import apps, config, picker


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return config.default_config()


def test_app_items_are_listed_first(monkeypatch):
    fake = [
        apps.App("a.desktop", "Alpha", "alpha", False, Path("/a.desktop")),
        apps.App("b.desktop", "Beta", "beta", False, Path("/b.desktop")),
    ]
    monkeypatch.setattr(picker.apps, "discover_apps", lambda: fake)
    items, by_id = picker.app_items()
    assert [key for key, _ in items] == ["a.desktop", "b.desktop"]
    assert "application" in items[0][1]
    assert by_id["a.desktop"].name == "Alpha"


def test_run_pick_launches_selected_application(cfg, monkeypatch):
    app = apps.App("a.desktop", "Alpha", "alpha", False, Path("/a.desktop"))
    launched = []
    monkeypatch.setattr(picker.launchers, "pick", lambda items, program: "a.desktop")
    monkeypatch.setattr(picker.apps, "launch", lambda app, **kw: launched.append(app.id))

    rc = picker.run_pick(cfg, [("a.desktop", "Alpha")], {"a.desktop": app})
    assert rc == 0
    assert launched == ["a.desktop"]


def test_run_pick_opens_selected_file_at_position(cfg, tmp_path, monkeypatch):
    target = tmp_path / "note.txt"
    target.write_text("hello", encoding="utf-8")
    opened = {}
    monkeypatch.setattr(picker.launchers, "pick", lambda items, program: str(target))
    monkeypatch.setattr(
        picker, "open_result", lambda openers, path, **kw: opened.update(path=path, **kw)
    )

    hits = {str(target): {"path": str(target), "line": 12, "page": None}}
    rc = picker.run_pick(cfg, [(str(target), "note.txt")], {}, hits_by_key=hits)
    assert rc == 0
    assert opened["path"] == target
    assert opened["line"] == 12


def test_run_pick_distinguishes_chunks_of_the_same_file(cfg, tmp_path, monkeypatch):
    """Two hits of one file must not collapse onto the same key/position."""
    target = tmp_path / "book.pdf"
    target.write_bytes(b"%PDF")
    opened = {}
    monkeypatch.setattr(picker.launchers, "pick", lambda items, program: "hit:1")
    monkeypatch.setattr(
        picker, "open_result", lambda openers, path, **kw: opened.update(path=path, **kw)
    )

    items = [("hit:0", "book.pdf (page 5)"), ("hit:1", "book.pdf (page 42)")]
    hits = {
        "hit:0": {"path": str(target), "line": None, "page": 5},
        "hit:1": {"path": str(target), "line": None, "page": 42},
    }
    assert picker.run_pick(cfg, items, {}, hits_by_key=hits) == 0
    assert opened["path"] == target
    assert opened["page"] == 42


def test_hit_key_is_unique_per_chunk(tmp_path):
    first = {"path": str(tmp_path / "a.txt"), "chunk_id": 7}
    second = {"path": str(tmp_path / "a.txt"), "chunk_id": 8}
    assert picker.hit_key(first, 0) != picker.hit_key(second, 1)
    assert str(tmp_path / "a.txt") in picker.hit_key(first, 0)
    # fall back to the index when a chunk id is missing
    assert picker.hit_key({"path": "x"}, 3) == "hit:3"


def test_run_pick_cancelled_returns_zero(cfg, monkeypatch):
    monkeypatch.setattr(picker.launchers, "pick", lambda items, program: None)
    assert picker.run_pick(cfg, [("a.desktop", "Alpha")], {}) == 0


def test_run_pick_empty_items(cfg):
    assert picker.run_pick(cfg, [], {}) == 0


def test_run_pick_missing_file_is_ignored(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(picker.launchers, "pick", lambda items, program: str(tmp_path / "gone"))
    monkeypatch.setattr(
        picker, "open_result", lambda *a, **k: pytest.fail("must not open a missing file")
    )
    assert picker.run_pick(cfg, [("x", "x")], {}) == 0


def test_run_pick_launch_failure_returns_one(cfg, monkeypatch):
    app = apps.App("a.desktop", "Alpha", "alpha", False, Path("/a.desktop"))
    monkeypatch.setattr(picker.launchers, "pick", lambda items, program: "a.desktop")

    def boom(app, **kw):
        raise OSError("nope")

    monkeypatch.setattr(picker.apps, "launch", boom)
    assert picker.run_pick(cfg, [("a.desktop", "Alpha")], {"a.desktop": app}) == 1


def test_run_pick_records_a_launch_for_frecency(cfg, monkeypatch):
    from feagle import frecency

    app = apps.App("a.desktop", "Alpha", "alpha", False, Path("/a.desktop"))
    monkeypatch.setattr(picker.launchers, "pick", lambda items, program: "a.desktop")
    monkeypatch.setattr(picker.apps, "launch", lambda app, **kw: None)

    assert picker.run_pick(cfg, [("a.desktop", "Alpha")], {"a.desktop": app}) == 0
    assert frecency.FrecencyStore().scores()["a.desktop"] == pytest.approx(1.0)


def test_run_pick_does_not_record_a_failed_launch(cfg, monkeypatch):
    from feagle import frecency

    app = apps.App("a.desktop", "Alpha", "alpha", False, Path("/a.desktop"))
    monkeypatch.setattr(picker.launchers, "pick", lambda items, program: "a.desktop")

    def boom(app, **kw):
        raise OSError("nope")

    monkeypatch.setattr(picker.apps, "launch", boom)
    assert picker.run_pick(cfg, [("a.desktop", "Alpha")], {"a.desktop": app}) == 1
    assert frecency.FrecencyStore().scores() == {}
