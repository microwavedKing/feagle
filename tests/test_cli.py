"""Tests for the command-line interface."""

from __future__ import annotations

import json

import pytest

from feagle import config, db
from feagle.cli import cmd_search
from feagle.cli.__main__ import main
from feagle.indexer import pipeline


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
    assert main(["index", "--root", str(docs), "--no-embed"]) == 0
    assert "reindexed=1" in capsys.readouterr().out

    assert main(["search", "semantic", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "fts"
    assert len(payload["results"]) == 1
    assert payload["results"][0]["path"].endswith("notes.txt")


def test_index_without_model_warns_but_succeeds(env, capsys):
    docs = make_docs(env)
    assert main(["index", "--root", str(docs)]) == 0
    captured = capsys.readouterr()
    assert "semantic indexing disabled" in captured.err
    assert "reindexed=1" in captured.out


def test_index_no_embed_is_silent(env, capsys):
    docs = make_docs(env)
    assert main(["index", "--root", str(docs), "--no-embed"]) == 0
    assert capsys.readouterr().err == ""


def test_search_human_output(env, capsys):
    docs = make_docs(env)
    main(["index", "--root", str(docs), "--no-embed"])
    capsys.readouterr()
    assert main(["search", "world"]) == 0
    out = capsys.readouterr().out
    assert "1. " in out
    assert "notes.txt" in out


def test_search_no_results(env, capsys):
    docs = make_docs(env)
    main(["index", "--root", str(docs), "--no-embed"])
    capsys.readouterr()
    assert main(["search", "zzzznotfound"]) == 0
    assert "No results." in capsys.readouterr().out


def test_status_json(env, capsys):
    docs = make_docs(env)
    main(["index", "--root", str(docs), "--no-embed"])
    capsys.readouterr()
    assert main(["status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["files"]["total"] == 1
    assert payload["chunks"] == 1
    assert payload["schema_version"] == 1
    assert payload["vectors"] == 0


def test_index_uses_configured_roots(env, capsys):
    docs = make_docs(env)
    write_config(env, f"[index]\nroots = ['{docs}']\n")
    assert main(["index", "--no-embed"]) == 0
    assert "reindexed=1" in capsys.readouterr().out


def test_rebuild_flag(env, capsys):
    docs = make_docs(env)
    main(["index", "--root", str(docs), "--no-embed"])
    capsys.readouterr()
    assert main(["index", "--root", str(docs), "--rebuild", "--no-embed"]) == 0
    assert "reindexed=1" in capsys.readouterr().out


def test_backfill_without_embeddings(env, capsys):
    docs = make_docs(env)
    main(["index", "--root", str(docs), "--no-embed"])
    capsys.readouterr()
    assert main(["index", "--backfill"]) == 1
    assert "backfill needs embeddings" in capsys.readouterr().err


def test_config_error_returns_two(env, capsys):
    write_config(env, "this is [not valid")
    assert main(["status"]) == 2
    assert "configuration error" in capsys.readouterr().err


# -- hybrid path with a stubbed embedder ----------------------------------


class _FakeEmbedder:
    dim = 3

    def embed_documents(self, items):
        return [[1.0, 0.0, 0.0] for _ in items]

    def embed_queries(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]


class _FakeService:
    def __init__(self, cfg):
        self.cfg = cfg

    def start(self):
        return _FakeEmbedder()

    def close(self, **kwargs):
        pass


def _seed_with_vectors(env):
    docs = env / "docs"
    docs.mkdir()
    (docs / "sem.txt").write_text("alpha semantic body", encoding="utf-8")
    (docs / "other.txt").write_text("beta unrelated words", encoding="utf-8")

    cfg = config.default_config()
    cfg["index"]["roots"] = [str(docs)]
    conn = db.open_database()
    pipeline.index_roots(conn, cfg, embedder=_FakeEmbedder())
    conn.close()


def test_search_hybrid_uses_vectors(env, capsys, monkeypatch):
    _seed_with_vectors(env)
    monkeypatch.setattr(cmd_search, "EmbeddingService", _FakeService)
    assert main(["search", "alpha", "--mode", "hybrid", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "hybrid"
    assert any(r["path"].endswith("sem.txt") for r in payload["results"])


def test_search_hybrid_degrades_without_model(env, capsys):
    docs = make_docs(env)
    main(["index", "--root", str(docs), "--no-embed"])
    capsys.readouterr()
    assert main(["search", "semantic", "--mode", "hybrid"]) == 0
    captured = capsys.readouterr()
    assert "semantic search unavailable" in captured.err
    assert "notes.txt" in captured.out


# -- open ------------------------------------------------------------------


def test_open_dry_run_uses_default_template(env, capsys):
    target = env / "note.txt"
    target.write_text("hi", encoding="utf-8")
    assert main(["open", str(target), "--dry-run"]) == 0
    assert capsys.readouterr().out.strip() == f"xdg-open {target}"


def test_open_pdf_with_page_uses_pdf_template(env, capsys):
    target = env / "doc.pdf"
    target.write_bytes(b"%PDF")
    assert main(["open", str(target), "--page", "3", "--dry-run"]) == 0
    assert capsys.readouterr().out.strip() == f"zathura --page 3 {target}"


def test_open_positional_plus_is_a_page_for_pdf(env, capsys):
    target = env / "doc.pdf"
    target.write_bytes(b"%PDF")
    assert main(["open", str(target), "+4", "--dry-run"]) == 0
    assert "zathura --page 4" in capsys.readouterr().out


def test_open_positional_plus_is_a_line_for_text(env, capsys):
    write_config(
        env,
        '[openers]\ndefault = "xdg-open {path}"\ntxt = "code --goto {path}:{line}"\n',
    )
    target = env / "note.txt"
    target.write_text("hi", encoding="utf-8")
    assert main(["open", str(target), "+12", "--dry-run"]) == 0
    assert f"code --goto {target}:12" in capsys.readouterr().out


def test_open_missing_file_fails(env, capsys):
    assert main(["open", str(env / "nope.txt")]) == 1
    assert "no such file" in capsys.readouterr().err
