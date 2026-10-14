"""Tests for systemd unit templates and installation."""

from __future__ import annotations

from pathlib import Path

import pytest

from feagle import units

REPO_UNITS = Path(__file__).resolve().parents[1] / "systemd"


@pytest.mark.parametrize("name", units.UNIT_NAMES)
def test_repo_unit_matches_rendered_template(name):
    repo_text = (REPO_UNITS / name).read_text(encoding="utf-8")
    assert repo_text == units.render_unit(name, "/usr/bin/feagle")


def test_indexerd_unit_is_low_priority():
    text = units.render_unit("feagle-indexerd.service", "/usr/bin/feagle")
    assert "Nice=19" in text
    assert "IOSchedulingClass=idle" in text
    assert "PYTHONUNBUFFERED=1" in text
    assert "Restart=on-failure" in text
    assert "WantedBy=default.target" in text
    # CPUQuota stays a comment so it does not mislead
    assert "#CPUQuota=200%" in text


def test_queryd_unit_has_no_nice():
    text = units.render_unit("feagle-queryd.service", "/usr/bin/feagle")
    assert "Nice=" not in text
    assert "ExecStart=/usr/bin/feagle queryd" in text


def test_unknown_unit_raises():
    with pytest.raises(KeyError, match="unknown unit"):
        units.render_unit("nope.service", "/usr/bin/feagle")


def test_install_writes_units(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    written = units.install("/opt/feagle/bin/feagle")

    assert units.are_installed() is True
    assert len(written) == 2
    for path in written:
        assert path.parent == units.user_unit_dir()
        text = path.read_text(encoding="utf-8")
        assert "ExecStart=/opt/feagle/bin/feagle" in text
        assert "{feagle}" not in text


def test_are_installed_false_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert units.are_installed() is False


def test_exec_path_prefers_invoked_script(tmp_path, monkeypatch):
    script = tmp_path / "bin" / "feagle"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr("sys.argv", [str(script), "setup"])
    assert units.exec_path() == str(script)


def test_exec_path_falls_back_to_venv_bin(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "python").write_text("", encoding="utf-8")
    (bindir / "feagle").write_text("", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["-m", "feagle.cli"])  # not a 'feagle' argv0
    monkeypatch.setattr("sys.executable", str(bindir / "python"))
    monkeypatch.setattr(units.shutil, "which", lambda name: None)
    assert units.exec_path() == str(bindir / "feagle")


def test_exec_path_uses_path_lookup(tmp_path, monkeypatch):
    script = tmp_path / "feagle"
    script.write_text("", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["feagle"])
    monkeypatch.setattr(units.shutil, "which", lambda name: str(script))
    assert units.exec_path() == str(script)


def test_exec_path_last_resort(monkeypatch):
    monkeypatch.setattr("sys.argv", ["feagle"])
    monkeypatch.setattr("sys.executable", "/nonexistent/python")
    monkeypatch.setattr(units.shutil, "which", lambda name: None)
    assert units.exec_path() == "feagle"
