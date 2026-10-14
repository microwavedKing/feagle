"""Tests for desktop-entry parsing and launching."""

from __future__ import annotations

from pathlib import Path

import pytest

from feagle import apps

ENTRY = """\
[Desktop Entry]
Type=Application
Name=Example Editor
Exec=example-editor %U
Terminal=false
"""


def write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_parse_basic_entry(tmp_path):
    path = write(tmp_path, "example.desktop", ENTRY)
    app = apps.parse_desktop_file(path, tmp_path)
    assert app is not None
    assert app.id == "example.desktop"
    assert app.name == "Example Editor"
    assert app.exec_line == "example-editor %U"
    assert app.terminal is False


def test_parse_subdirectory_gets_dashed_id(tmp_path):
    path = write(tmp_path, "kde/thing.desktop", ENTRY)
    app = apps.parse_desktop_file(path, tmp_path)
    assert app is not None
    assert app.id == "kde-thing.desktop"


@pytest.mark.parametrize(
    "line",
    [
        "NoDisplay=true",
        "Hidden=true",
        "[Desktop Entry]\nType=Link\nName=X\nExec=x",
    ],
)
def test_entries_are_skipped(tmp_path, line):
    body = ENTRY + line + "\n" if "[" not in line else line
    path = write(tmp_path, "skip.desktop", body)
    assert apps.parse_desktop_file(path, tmp_path) is None


def test_missing_name_or_exec_is_skipped(tmp_path):
    path = write(tmp_path, "broken.desktop", "[Desktop Entry]\nType=Application\nName=Only\n")
    assert apps.parse_desktop_file(path, tmp_path) is None


def test_tryexec_missing_binary_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(apps.shutil, "which", lambda name: None)
    path = write(tmp_path, "try.desktop", ENTRY + "TryExec=definitely-not-here\n")
    assert apps.parse_desktop_file(path, tmp_path) is None


def test_other_sections_are_ignored(tmp_path):
    body = ENTRY + "\n[Desktop Action new]\nName=New Window\nExec=example-editor --new\n"
    path = write(tmp_path, "sections.desktop", body)
    app = apps.parse_desktop_file(path, tmp_path)
    assert app is not None and app.name == "Example Editor"


def test_discover_apps_prefers_user_entries(tmp_path):
    user = tmp_path / "user"
    system = tmp_path / "system"
    write(user, "same.desktop", ENTRY.replace("Example Editor", "User Editor"))
    write(system, "same.desktop", ENTRY.replace("Example Editor", "System Editor"))
    write(system, "other.desktop", ENTRY.replace("Example Editor", "Another App"))

    found = apps.discover_apps([user, system])
    by_id = {app.id: app.name for app in found}
    assert by_id["same.desktop"] == "User Editor"
    assert by_id["other.desktop"] == "Another App"
    assert [app.name for app in found] == sorted(
        [app.name for app in found], key=str.casefold
    )


def test_exec_argv_strips_field_codes():
    app = apps.App("x.desktop", "X", 'prog --flag "%f" %U %i', False, Path("/x.desktop"))
    assert apps.exec_argv(app) == ["prog", "--flag"]


def test_launch_command_prefers_gtk_launch(monkeypatch):
    monkeypatch.setattr(apps.shutil, "which", lambda name: f"/usr/bin/{name}")
    app = apps.App("x.desktop", "X", "prog", False, Path("/x.desktop"))
    assert apps.launch_command(app) == ["gtk-launch", "x.desktop"]


def test_launch_command_falls_back_to_exec(monkeypatch):
    monkeypatch.setattr(apps.shutil, "which", lambda name: None)
    app = apps.App("x.desktop", "X", "prog %U", False, Path("/x.desktop"))
    assert apps.launch_command(app) == ["prog"]


def test_launch_command_wraps_terminal(monkeypatch):
    def which(name):
        return "/usr/bin/foot" if name == "foot" else None

    monkeypatch.setattr(apps.shutil, "which", which)
    app = apps.App("t.desktop", "T", "htop", True, Path("/t.desktop"))
    assert apps.launch_command(app) == ["foot", "htop"]


def test_launch_uses_injected_spawn(monkeypatch):
    monkeypatch.setattr(apps.shutil, "which", lambda name: None)
    calls = []
    app = apps.App("x.desktop", "X", "prog", False, Path("/x.desktop"))
    used = apps.launch(app, spawn=lambda cmd: calls.append(cmd))
    assert used == ["prog"]
    assert calls == [["prog"]]


def test_launch_without_command_raises(monkeypatch):
    monkeypatch.setattr(apps.shutil, "which", lambda name: None)
    app = apps.App("x.desktop", "X", "%U", False, Path("/x.desktop"))
    with pytest.raises(OSError):
        apps.launch(app, spawn=lambda cmd: None)
