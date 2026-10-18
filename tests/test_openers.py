"""Tests for opener template selection and command building."""

from __future__ import annotations

from pathlib import Path

import pytest

from feagle import openers

OPENERS = {
    "default": "xdg-open {path}",
    "pdf": "zathura --page {page} {path}",
    "py": "code --goto {path}:{line}",
}


def test_select_template_by_extension():
    assert openers.select_template(OPENERS, Path("/a/b.pdf")) == OPENERS["pdf"]
    assert openers.select_template(OPENERS, Path("/a/b.py")) == OPENERS["py"]
    assert openers.select_template(OPENERS, Path("/a/b.txt")) == OPENERS["default"]


def test_default_template_keeps_path():
    assert openers.build_command("xdg-open {path}", path=Path("/a/b.txt")) == [
        "xdg-open",
        "/a/b.txt",
    ]


def test_pdf_template_with_page():
    command = openers.build_command(OPENERS["pdf"], path=Path("/a/b.pdf"), page=7)
    assert command == ["zathura", "--page", "7", "/a/b.pdf"]


def test_absent_page_drops_flag_and_placeholder():
    assert openers.build_command(OPENERS["pdf"], path=Path("/a/b.pdf")) == [
        "zathura",
        "/a/b.pdf",
    ]


def test_braces_in_the_path_are_kept():
    """A path is data, never a template."""
    path = Path("/a/{draft}/b{1}.txt")
    assert openers.build_command("xdg-open {path}", path=path) == ["xdg-open", str(path)]


def test_literal_braces_in_the_template_survive():
    template = "sh -c 'awk {print $1}' {path}"
    command = openers.build_command(template, path=Path("/a/b.txt"))
    assert command == ["sh", "-c", "awk {print $1}", "/a/b.txt"]


def test_an_unknown_placeholder_is_not_a_crash():
    command = openers.build_command("echo {pth} {path}", path=Path("/a/b.txt"))
    assert command == ["echo", "{pth}", "/a/b.txt"]


def test_a_format_spec_on_a_placeholder_still_works():
    command = openers.build_command(
        "zathura --page {page:>3} {path}", path=Path("/a/b.pdf"), page=7
    )
    assert command == ["zathura", "--page", "  7", "/a/b.pdf"]


def test_line_placeholder_inside_token():
    command = openers.build_command(OPENERS["py"], path=Path("/a/b.py"), line=42)
    assert command == ["code", "--goto", "/a/b.py:42"]
    assert openers.build_command(OPENERS["py"], path=Path("/a/b.py")) == ["code"]


def test_equals_style_placeholder_is_dropped():
    command = openers.build_command("zathura --page={page} {path}", path=Path("/a.pdf"))
    assert command == ["zathura", "/a.pdf"]


def test_paths_with_spaces_survive():
    command = openers.build_command("xdg-open {path}", path=Path("/a dir/b file.txt"))
    assert command == ["xdg-open", "/a dir/b file.txt"]


def test_open_result_uses_spawn_without_shell():
    calls = []

    def spawn(cmd):
        calls.append(cmd)
        return object()

    used = openers.open_result(OPENERS, Path("/a/b.pdf"), page=3, spawn=spawn)
    assert used == ["zathura", "--page", "3", "/a/b.pdf"]
    assert calls == [used]


def test_open_result_missing_binary_raises():
    def spawn(cmd):
        raise FileNotFoundError("no such file")

    with pytest.raises(openers.OpenError, match="not found"):
        openers.open_result(OPENERS, Path("/a/b.txt"), spawn=spawn)


def test_empty_template_raises():
    with pytest.raises(openers.OpenError, match="empty command"):
        openers.open_result({"default": "   "}, Path("/a/b.txt"), spawn=lambda cmd: None)


def test_time_placeholder_is_seconds():
    command = openers.build_command(
        "mpv --start={time} {path}", path=Path("/a/b.mp4"), time_ms=90000
    )
    assert command == ["mpv", "--start=90", "/a/b.mp4"]


def test_absent_time_drops_flag_and_placeholder():
    command = openers.build_command("mpv --start {time} {path}", path=Path("/a/b.mp4"))
    assert command == ["mpv", "/a/b.mp4"]
