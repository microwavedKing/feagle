"""Tests for the command-line interface."""

from __future__ import annotations

import json

import pytest

from feagle.cli.__main__ import main


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    return tmp_path


def write_config(env, body):
    cfg_dir = env / "cfg" / "feagle"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.toml").write_text(body, encoding="utf-8")


def make_docs(env):
    docs = env / "docs"
    docs.mkdir()
    (docs / "notes.txt").write_text("the semantic world of search", encoding="utf-8")
    return docs


def test_status_without_index(env, capsys):
    assert main(["status"]) == 1
    assert "no index" in capsys.readouterr().err


def test_search_without_index(env, capsys):
    assert main(["search", "anything"]) == 1
    assert "no index" in capsys.readouterr().err


def test_index_without_roots_fails(env, capsys):
    assert main(["index"]) == 1
    assert "no roots configured" in capsys.readouterr().err


def test_index_with_root_then_search_json(env, capsys):
    docs = make_docs(env)
    assert main(["index", "--root", str(docs)]) == 0
    assert "reindexed=1" in capsys.readouterr().out

    assert main(["search", "semantic", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "fts"
    assert len(payload["results"]) == 1
    assert payload["results"][0]["path"].endswith("notes.txt")


def test_search_human_output(env, capsys):
    docs = make_docs(env)
    main(["index", "--root", str(docs)])
    capsys.readouterr()
    assert main(["search", "world"]) == 0
    out = capsys.readouterr().out
    assert "1. " in out
    assert "notes.txt" in out


def test_search_no_results(env, capsys):
    docs = make_docs(env)
    main(["index", "--root", str(docs)])
    capsys.readouterr()
    assert main(["search", "zzzznotfound"]) == 0
    assert "No results." in capsys.readouterr().out


def test_status_json(env, capsys):
    docs = make_docs(env)
    main(["index", "--root", str(docs)])
    capsys.readouterr()
    assert main(["status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["files"]["total"] == 1
    assert payload["chunks"] == 1
    assert payload["schema_version"] == 1


def test_index_uses_configured_roots(env, capsys):
    docs = make_docs(env)
    write_config(env, f"[index]\nroots = ['{docs}']\n")
    assert main(["index"]) == 0
    assert "reindexed=1" in capsys.readouterr().out


def test_rebuild_flag(env, capsys):
    docs = make_docs(env)
    main(["index", "--root", str(docs)])
    capsys.readouterr()
    assert main(["index", "--root", str(docs), "--rebuild"]) == 0
    assert "reindexed=1" in capsys.readouterr().out


def test_config_error_returns_two(env, capsys):
    write_config(env, "this is [not valid")
    assert main(["status"]) == 2
    assert "configuration error" in capsys.readouterr().err
