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
