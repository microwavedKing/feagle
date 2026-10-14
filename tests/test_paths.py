"""Tests for XDG path resolution."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from feagle import paths


def test_config_home_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert paths.config_home() == tmp_path / ".config"


def test_config_home_absolute_override(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert paths.config_home() == tmp_path / "cfg"


def test_relative_env_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", "relative/path")
    assert paths.data_home() == tmp_path / ".local" / "share"


def test_data_and_state_fallbacks(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    assert paths.data_home() == tmp_path / ".local" / "share"
    assert paths.state_home() == tmp_path / ".local" / "state"


def test_runtime_dir_fallback_uses_uid(monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    assert paths.runtime_dir() == Path(f"/run/user/{os.getuid()}")


def test_derived_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))

    assert paths.config_file() == tmp_path / "cfg" / "feagle" / "config.toml"
    assert paths.db_path() == tmp_path / "data" / "feagle" / "feagle.sqlite"
    assert paths.models_dir() == tmp_path / "data" / "feagle" / "models"
    assert paths.socket_path() == tmp_path / "run" / "feagle" / "queryd.sock"


def test_ensure_data_dirs_creates_private_dirs(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    paths.ensure_data_dirs()

    data_dir = paths.data_dir()
    assert data_dir.is_dir()
    assert paths.models_dir().is_dir()
    assert stat.S_IMODE(data_dir.stat().st_mode) == 0o700


def test_ensure_runtime_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    paths.ensure_runtime_dir()
    assert paths.runtime_subdir().is_dir()
    assert stat.S_IMODE(paths.runtime_subdir().stat().st_mode) == 0o700
