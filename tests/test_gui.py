"""Tests for the popup's gui.toml: lenient reads, strict writes."""

from __future__ import annotations

import json
import logging
import stat
import tomllib
from typing import TYPE_CHECKING

import pytest

from feagle import gui

if TYPE_CHECKING:
    from pathlib import Path


def _file(tmp_path: Path) -> Path:
    return tmp_path / "gui.toml"


def test_defaults_when_the_file_is_missing(tmp_path):
    assert gui.load_gui(_file(tmp_path)) == gui.default_gui()


def test_gui_file_follows_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert gui.gui_file() == tmp_path / "feagle" / "gui.toml"


def test_round_trip(tmp_path):
    path = _file(tmp_path)
    values = gui.set_gui({"theme": "light", "preview_width": 500}, path)

    assert values["theme"] == "light"
    assert values["preview_width"] == 500
    assert values["layout"] == "detailed"  # untouched keys survive
    assert gui.load_gui(path) == values
    assert tomllib.loads(path.read_text(encoding="utf-8"))["popup"]["theme"] == "light"


def test_the_file_is_private_and_leaves_no_temp_files(tmp_path):
    path = _file(tmp_path)
    gui.save_gui(gui.default_gui(), path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [entry.name for entry in tmp_path.iterdir()] == ["gui.toml"]


def test_a_flat_file_is_read_too(tmp_path):
    path = _file(tmp_path)
    path.write_text('theme = "light"\nrow_columns = 2\n', encoding="utf-8")
    values = gui.load_gui(path)
    assert values["theme"] == "light"
    assert values["row_columns"] == 2


def test_unknown_setting_is_ignored_with_a_warning(tmp_path, caplog):
    path = _file(tmp_path)
    path.write_text("[popup]\nnonsense = 1\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="feagle.gui"):
        values = gui.load_gui(path)
    assert values == gui.default_gui()
    assert any("nonsense" in record.message for record in caplog.records)


def test_unknown_section_is_reported(tmp_path, caplog):
    path = _file(tmp_path)
    path.write_text("[popup]\ntheme = 'light'\n[elsewhere]\nx = 1\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="feagle.gui"):
        values = gui.load_gui(path)
    assert values["theme"] == "light"
    assert any("elsewhere" in record.message for record in caplog.records)


@pytest.mark.parametrize(
    ("text", "key", "expected"),
    [
        ("preview_width = 5000\n", "preview_width", 360),
        ("row_columns = 0\n", "row_columns", 3),
        ('theme = "solarized"\n', "theme", "dark"),
        ('debounce_ms = "soon"\n', "debounce_ms", 120),
        ('show_preview = "maybe"\n', "show_preview", True),
    ],
)
def test_a_bad_value_falls_back_to_the_default(tmp_path, text, key, expected):
    path = _file(tmp_path)
    path.write_text(f"[popup]\n{text}", encoding="utf-8")
    assert gui.load_gui(path)[key] == expected


def test_broken_toml_falls_back_to_defaults(tmp_path, caplog):
    path = _file(tmp_path)
    path.write_text("[popup\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="feagle.gui"):
        assert gui.load_gui(path) == gui.default_gui()
    assert caplog.records


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("show_preview", "false", False),
        ("show_preview", True, True),
        ("preview_width", "500", 500),
        ("apps_limit", 0, 0),
        ("theme", "light", "light"),
        ("row_columns", 1, 1),
    ],
)
def test_coerce_accepts(key, value, expected):
    assert gui.coerce(key, value) == expected


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("nope", 1),  # not a setting at all
        ("show_preview", "maybe"),
        ("show_preview", 1),  # 1 is not "true"
        ("preview_width", 100),  # below the minimum
        ("preview_width", 900),  # above the maximum
        ("preview_width", True),  # a bool is not an integer
        ("preview_width", "wide"),
        ("row_columns", 4),
        ("theme", "Solarized"),  # choices are case-sensitive
        ("default_filter", "app"),
        ("debounce_ms", 5000),
    ],
)
def test_coerce_rejects(key, value):
    with pytest.raises(gui.GuiError):
        gui.coerce(key, value)


def test_set_gui_refuses_before_writing(tmp_path):
    path = _file(tmp_path)
    gui.set_gui({"theme": "light"}, path)
    before = path.read_text(encoding="utf-8")

    with pytest.raises(gui.GuiError):
        gui.set_gui({"theme": "light", "row_columns": 99}, path)
    assert path.read_text(encoding="utf-8") == before
    assert gui.load_gui(path)["row_columns"] == 3


def test_set_gui_needs_a_change(tmp_path):
    with pytest.raises(gui.GuiError):
        gui.set_gui({}, _file(tmp_path))


def test_a_bad_value_on_disk_is_repaired_by_the_next_write(tmp_path):
    path = _file(tmp_path)
    path.write_text('[popup]\ntheme = "solarized"\n', encoding="utf-8")

    values = gui.set_gui({"preview_width": 400}, path)
    assert values["theme"] == "dark"
    assert gui.load_gui(path)["theme"] == "dark"


def test_schema_covers_every_setting_and_is_json_ready():
    schema = gui.schema()

    assert set(schema) == set(gui.SETTINGS)
    json.dumps(schema)  # the daemon puts it on the socket as JSON
    assert schema["row_columns"]["min"] == 1
    assert schema["row_columns"]["max"] == 3
    assert schema["theme"]["choices"] == ["dark", "light"]
    assert schema["show_preview"]["kind"] == "bool"
