"""Tests for the setup helpers and the non-interactive setup command."""

from __future__ import annotations

import tomllib

import pytest

from feagle import config, setup
from feagle.cli.__main__ import main


def test_to_toml_round_trips():
    cfg = config.default_config()
    cfg["index"]["roots"] = ["/home/user/docs", "/home/user/notes"]
    cfg["embeddings"]["mrl_dim"] = 256
    text = setup.to_toml(cfg)
    assert tomllib.loads(text) == cfg


def test_to_toml_escapes_strings():
    cfg = config.default_config()
    cfg["openers"]["default"] = 'prog "quoted" {path}'
    assert tomllib.loads(setup.to_toml(cfg))["openers"]["default"] == 'prog "quoted" {path}'


def test_write_config_creates_backup(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[index]\nroots = []\n", encoding="utf-8")
    cfg = config.default_config()
    cfg["index"]["roots"] = ["/docs"]

    written, backup = setup.write_config(cfg, path)
    assert written == path
    assert backup == path.with_name("config.toml.bak")
    assert backup.read_text(encoding="utf-8").startswith("[index]")
    assert tomllib.loads(path.read_text(encoding="utf-8"))["index"]["roots"] == ["/docs"]


def test_apply_plan_expands_and_dedupes(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "docs").mkdir()
    cfg = config.default_config()
    plan = setup.SetupPlan(
        roots=[str(tmp_path / "docs"), str(tmp_path / "docs")],
        model="embeddinggemma-2-bf16",
        mrl_dim=256,
        launcher="fuzzel",
    )
    updated = setup.apply_plan(cfg, plan)
    assert updated["index"]["roots"] == [str(tmp_path / "docs")]
    assert updated["embeddings"]["model"] == "embeddinggemma-2-bf16"
    assert updated["embeddings"]["mrl_dim"] == 256
    assert updated["launcher"]["program"] == "fuzzel"
    # original untouched
    assert cfg["index"]["roots"] == []


def test_default_roots_only_existing(tmp_path):
    (tmp_path / "Documents").mkdir()
    roots = setup.default_roots(home=tmp_path)
    assert roots == [str(tmp_path / "Documents")]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    return tmp_path


def test_setup_non_interactive_writes_config(env, capsys):
    docs = env / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("hello", encoding="utf-8")

    rc = main([
        "setup", "--yes",
        "--root", str(docs),
        "--model", "embeddinggemma-2-q8",
        "--mrl-dim", "256",
        "--launcher", "stdout",
        "--no-download", "--no-units", "--no-index",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "wrote config" in out
    assert "feagle pick" in out
    assert "later phase" not in out

    cfg = config.load_config()
    assert cfg["index"]["roots"] == [str(docs)]
    assert cfg["embeddings"]["mrl_dim"] == 256
    assert cfg["launcher"]["program"] == "stdout"


def test_gather_plan_defaults_to_the_configured_model(monkeypatch):
    import argparse

    from feagle.cli import cmd_setup

    cfg = config.default_config()
    cfg["index"]["roots"] = ["/docs"]
    cfg["launcher"]["program"] = "stdout"
    cfg["embeddings"]["model"] = "embeddinggemma-2-q8"

    monkeypatch.setattr(cmd_setup, "_prompt", lambda text, default="": default)
    monkeypatch.setattr(cmd_setup, "_prompt_yes_no", lambda text, default=True: default)

    args = argparse.Namespace(
        root=None, model=None, mrl_dim=None, launcher=None,
        no_download=False, no_units=False, no_enable=False,
        no_index=False, no_backup=False, yes=False,
    )
    plan = cmd_setup._gather_plan(args, cfg)
    # the interactive default must be the configured model, not the
    # alphabetically-first preset (embeddinggemma-2-bf16)
    assert plan.model == "embeddinggemma-2-q8"


def test_setup_without_roots_fails(env, capsys, monkeypatch):
    monkeypatch.setenv("HOME", str(env / "home"))
    (env / "home").mkdir()
    rc = main(["setup", "--yes", "--no-download", "--no-units", "--no-index"])
    assert rc == 1
    assert "no directories to index" in capsys.readouterr().err


def test_setup_installs_units_when_asked(env, capsys):
    docs = env / "docs"
    docs.mkdir()
    rc = main([
        "setup", "--yes", "--root", str(docs),
        "--no-download", "--no-index", "--no-backup", "--no-enable",
    ])
    assert rc == 0
    assert "installed units" in capsys.readouterr().out
    from feagle import units

    assert units.are_installed() is True
