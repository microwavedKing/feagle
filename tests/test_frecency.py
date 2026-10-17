"""Tests for the launcher's launch history (frecency)."""

from __future__ import annotations

import json

from feagle import frecency


class Clock:
    """A controllable clock for deterministic decay tests."""

    def __init__(self, value: float = 1_700_000_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, days: float) -> None:
        self.value += days * 86400.0


def test_score_halves_after_one_half_life():
    entry = frecency.Entry(count=4, last=1_000_000.0)
    assert frecency.score(entry, 1_000_000.0) == 4.0
    assert frecency.score(entry, 1_000_000.0 + 30 * 86400) == 2.0
    assert frecency.score(entry, 1_000_000.0 + 60 * 86400) == 1.0


def test_score_never_goes_negative_for_future_stamps():
    entry = frecency.Entry(count=1, last=2_000_000.0)
    assert frecency.score(entry, 1_000_000.0) == 1.0


def test_record_counts_uses_and_persists(tmp_path):
    clock = Clock()
    path = tmp_path / "frecency.json"
    store = frecency.FrecencyStore(path, clock=clock)
    store.record("firefox.desktop")
    store.record("firefox.desktop")
    store.record("vim.desktop")

    # A fresh store (as the daemon would build) sees the same history.
    reopened = frecency.FrecencyStore(path, clock=clock)
    scores = reopened.scores()
    assert scores["firefox.desktop"] == 2.0
    assert scores["vim.desktop"] == 1.0


def test_record_writes_owner_only_file(tmp_path):
    path = tmp_path / "state" / "feagle" / "frecency.json"
    store = frecency.FrecencyStore(path, clock=Clock())
    store.record("a.desktop")
    assert path.exists()
    assert (path.stat().st_mode & 0o777) == 0o600


def test_missing_file_is_empty(tmp_path):
    store = frecency.FrecencyStore(tmp_path / "nope.json", clock=Clock())
    assert store.scores() == {}


def test_corrupt_file_is_ignored(tmp_path):
    path = tmp_path / "frecency.json"
    path.write_text("{not json", encoding="utf-8")
    store = frecency.FrecencyStore(path, clock=Clock())
    assert store.scores() == {}
    store.record("a.desktop")  # still usable: the bad file is replaced
    assert frecency.FrecencyStore(path, clock=Clock()).scores()["a.desktop"] == 1.0


def test_malformed_entries_are_skipped(tmp_path):
    path = tmp_path / "frecency.json"
    path.write_text(
        json.dumps(
            {
                "good.desktop": {"count": 2, "last": 1_700_000_000.0},
                "no_count.desktop": {"last": 1_700_000_000.0},
                "bad_count.desktop": {"count": "many", "last": 1.0},
                "not_a_dict": 5,
            }
        ),
        encoding="utf-8",
    )
    assert set(frecency.FrecencyStore(path, clock=Clock()).scores()) == {"good.desktop"}


def test_store_reloads_when_another_process_writes(tmp_path):
    clock = Clock()
    path = tmp_path / "frecency.json"
    daemon = frecency.FrecencyStore(path, clock=clock)
    daemon.scores()  # load (empty) and cache

    cli = frecency.FrecencyStore(path, clock=clock)
    cli.record("picked.desktop")

    assert daemon.scores()["picked.desktop"] == 1.0


def test_stale_entries_are_pruned(tmp_path):
    clock = Clock()
    path = tmp_path / "frecency.json"
    store = frecency.FrecencyStore(path, clock=clock)
    store.record("ancient.desktop")
    clock.advance(400)
    store.record("fresh.desktop")

    scores = frecency.FrecencyStore(path, clock=clock).scores()
    assert "ancient.desktop" not in scores
    assert "fresh.desktop" in scores


def test_entries_are_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(frecency, "MAX_ENTRIES", 3)
    clock = Clock()
    store = frecency.FrecencyStore(tmp_path / "frecency.json", clock=clock)
    for i in range(5):
        store.record(f"app{i}.desktop")
        clock.advance(0.1)
    assert len(store.scores()) == 3
    # The most recent (highest-scoring) entries are the ones kept.
    assert set(store.scores()) == {"app2.desktop", "app3.desktop", "app4.desktop"}


def test_record_ignores_empty_key(tmp_path):
    path = tmp_path / "frecency.json"
    frecency.FrecencyStore(path, clock=Clock()).record("")
    assert not path.exists()
