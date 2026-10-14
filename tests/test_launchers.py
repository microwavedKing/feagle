"""Tests for the launcher adapters."""

from __future__ import annotations

import io

import pytest

from feagle import launchers


def _which_installed(*names):
    return lambda name: f"/usr/bin/{name}" if name in names else None


def test_available_stdout_is_always_available():
    assert launchers.available("stdout") is True


def test_detect_auto_prefers_fuzzel(monkeypatch):
    monkeypatch.setattr(launchers.shutil, "which", _which_installed("wofi", "fuzzel"))
    assert launchers.detect("auto") == "fuzzel"


def test_detect_auto_falls_back_to_stdout(monkeypatch):
    monkeypatch.setattr(launchers.shutil, "which", _which_installed())
    assert launchers.detect("auto") == "stdout"


def test_detect_explicit_program(monkeypatch):
    monkeypatch.setattr(launchers.shutil, "which", _which_installed("tofi"))
    assert launchers.detect("tofi") == "tofi"


def test_detect_explicit_missing_falls_back(monkeypatch):
    monkeypatch.setattr(launchers.shutil, "which", _which_installed("wofi"))
    assert launchers.detect("walker") == "stdout"


def test_fuzzel_uses_columns():
    command = launchers.command_for("fuzzel")
    assert "--dmenu" in command
    assert "--with-nth=2" in command
    assert "--accept-nth=1" in command
    assert launchers.returns_path("fuzzel") is True


def test_other_launchers_return_the_whole_line():
    for name in ("wofi", "rofi", "tofi", "walker"):
        assert launchers.returns_path(name) is False


def test_unknown_launcher_raises():
    with pytest.raises(KeyError, match="unknown launcher"):
        launchers.command_for("dmenu")


def test_pick_parses_tab_separated_output(monkeypatch):
    monkeypatch.setattr(launchers.shutil, "which", _which_installed("wofi"))
    seen = {}

    def runner(command, text):
        seen["command"] = command
        seen["text"] = text
        return "docs/b.txt\tsome snippet\n"

    choice = launchers.pick([("docs/a.txt", "a"), ("docs/b.txt", "b")], runner=runner)
    assert choice == "docs/b.txt"
    assert seen["text"] == "docs/a.txt\ta\ndocs/b.txt\tb\n"
    assert seen["command"][0] == "wofi"


def test_pick_handles_fuzzel_path_only(monkeypatch):
    monkeypatch.setattr(launchers.shutil, "which", _which_installed("fuzzel"))
    choice = launchers.pick(
        [("docs/a.txt", "a")], program="auto", runner=lambda cmd, text: "docs/a.txt\n"
    )
    assert choice == "docs/a.txt"


def test_pick_empty_output_means_cancelled(monkeypatch):
    monkeypatch.setattr(launchers.shutil, "which", _which_installed("wofi"))
    assert launchers.pick([("a", "a")], runner=lambda cmd, text: "") is None


def test_pick_no_items_is_none():
    assert launchers.pick([]) is None


def test_pick_falls_back_to_stdout_when_binary_vanishes(monkeypatch):
    monkeypatch.setattr(launchers.shutil, "which", _which_installed("wofi"))

    def runner(command, text):
        raise FileNotFoundError("gone")

    out = io.StringIO()
    choice = launchers.pick(
        [("a.txt", "a"), ("b.txt", "b")], runner=runner, out=out, read_line=lambda: "2\n"
    )
    assert choice == "b.txt"
    assert "1. a.txt" in out.getvalue()


def test_stdout_picker(monkeypatch):
    monkeypatch.setattr(launchers.shutil, "which", _which_installed())
    out = io.StringIO()
    items = [("a.txt", "first"), ("b.txt", "second")]

    assert launchers.pick(items, out=out, read_line=lambda: "2\n") == "b.txt"
    assert launchers.pick(items, out=out, read_line=lambda: "\n") is None
    assert launchers.pick(items, out=out, read_line=lambda: "nope\n") is None
    assert launchers.pick(items, out=out, read_line=lambda: "9\n") is None
    assert "second" in out.getvalue()
