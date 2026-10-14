"""Tests for the command-line interface."""

from __future__ import annotations

import json

import pytest

from feagle import config, db
from feagle.cli import cmd_search
from feagle.cli.__main__ import main
from feagle.embeddings.client import ServerUnavailable
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
    assert payload["schema_version"] == 2
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


def test_main_configures_logging(env, monkeypatch):
    called = {}
    monkeypatch.setattr(
        "feagle.cli.__main__.setup_logging", lambda *a, **k: called.setdefault("yes", True)
    )
    main(["status"])
    assert called.get("yes") is True


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


# -- search --pick ---------------------------------------------------------


def test_search_pick_opens_the_selected_result(env, capsys, monkeypatch):
    docs = make_docs(env)
    write_config(env, '[launcher]\nprogram = "stdout"\n')
    main(["index", "--root", str(docs), "--no-embed"])
    capsys.readouterr()

    import io
    import sys

    monkeypatch.setattr(sys, "stdin", io.StringIO("1\n"))
    opened = {}

    def fake_open(openers, path, **kwargs):
        opened["path"] = path
        opened["kwargs"] = kwargs
        return []

    monkeypatch.setattr("feagle.picker.open_result", fake_open)
    assert main(["search", "semantic", "--pick"]) == 0
    assert opened["path"].name == "notes.txt"


def test_search_pick_without_results_does_nothing(env, capsys, monkeypatch):
    docs = make_docs(env)
    write_config(env, '[launcher]\nprogram = "stdout"\n')
    main(["index", "--root", str(docs), "--no-embed"])
    capsys.readouterr()

    def fail(*args, **kwargs):
        raise AssertionError("must not open anything")

    monkeypatch.setattr("feagle.picker.open_result", fail)
    assert main(["search", "zzzznotfound", "--pick"]) == 0


# -- pick (applications + files) -------------------------------------------


def _fake_app():
    from pathlib import Path as _Path

    from feagle import apps

    return apps.App("fake.desktop", "Fake App", "fake", False, _Path("/fake.desktop"))


def test_pick_opens_a_file_without_a_query(env, capsys, monkeypatch):
    docs = make_docs(env)
    write_config(env, '[launcher]\nprogram = "stdout"\n')
    main(["index", "--root", str(docs), "--no-embed"])
    capsys.readouterr()

    import io
    import sys

    monkeypatch.setattr("feagle.picker.apps.discover_apps", lambda: [])
    monkeypatch.setattr(sys, "stdin", io.StringIO("1\n"))
    opened = {}
    monkeypatch.setattr(
        "feagle.picker.open_result", lambda openers, path, **kw: opened.update(path=path)
    )
    assert main(["pick"]) == 0
    assert opened["path"].name == "notes.txt"


def test_pick_lists_applications_first(env, capsys, monkeypatch):
    write_config(env, '[launcher]\nprogram = "stdout"\n')
    import io
    import sys

    monkeypatch.setattr("feagle.picker.apps.discover_apps", lambda: [_fake_app()])
    monkeypatch.setattr(sys, "stdin", io.StringIO("1\n"))
    launched = []
    monkeypatch.setattr("feagle.picker.apps.launch", lambda app, **kw: launched.append(app.id))
    assert main(["pick", "--no-files"]) == 0
    assert launched == ["fake.desktop"]


def test_pick_with_query_lists_semantic_results(env, capsys, monkeypatch):
    docs = make_docs(env)
    write_config(env, '[launcher]\nprogram = "stdout"\n')
    main(["index", "--root", str(docs), "--no-embed"])
    capsys.readouterr()

    import io
    import sys

    monkeypatch.setattr("feagle.picker.apps.discover_apps", lambda: [])
    monkeypatch.setattr(sys, "stdin", io.StringIO("1\n"))
    opened = {}
    monkeypatch.setattr(
        "feagle.picker.open_result", lambda openers, path, **kw: opened.update(path=path)
    )
    assert main(["pick", "--query", "semantic"]) == 0
    assert opened["path"].name == "notes.txt"


def test_pick_without_index_still_shows_applications(env, capsys, monkeypatch):
    write_config(env, '[launcher]\nprogram = "stdout"\n')
    import io
    import sys

    monkeypatch.setattr("feagle.picker.apps.discover_apps", lambda: [_fake_app()])
    monkeypatch.setattr(sys, "stdin", io.StringIO("1\n"))
    launched = []
    monkeypatch.setattr("feagle.picker.apps.launch", lambda app, **kw: launched.append(app.id))
    assert main(["pick"]) == 0  # no index, but the picker still works
    assert launched == ["fake.desktop"]
    assert "no index yet" in capsys.readouterr().err


def test_search_pick_with_apps_puts_applications_first(env, capsys, monkeypatch):
    docs = make_docs(env)
    write_config(env, '[launcher]\nprogram = "stdout"\n')
    main(["index", "--root", str(docs), "--no-embed"])
    capsys.readouterr()

    import io
    import sys

    monkeypatch.setattr("feagle.picker.apps.discover_apps", lambda: [_fake_app()])
    monkeypatch.setattr(sys, "stdin", io.StringIO("1\n"))
    launched = []
    monkeypatch.setattr("feagle.picker.apps.launch", lambda app, **kw: launched.append(app.id))
    assert main(["search", "semantic", "--pick", "--with-apps"]) == 0
    assert launched == ["fake.desktop"]


def test_search_pick_opens_the_selected_chunk(env, capsys, monkeypatch):
    """Several chunks of one file stay separately selectable."""
    docs = make_docs(env)
    write_config(env, '[launcher]\nprogram = "stdout"\n')
    main(["index", "--root", str(docs), "--no-embed"])
    capsys.readouterr()

    import io
    import sys

    target = docs / "notes.txt"
    fake = {
        "ok": True,
        "mode": "hybrid",
        "took_ms": 1.0,
        "results": [
            {"rank": 1, "score": 1.0, "path": str(target), "page": None,
             "line": 3, "snippet": "first", "chunk_id": 11},
            {"rank": 2, "score": 0.9, "path": str(target), "page": None,
             "line": 9, "snippet": "second", "chunk_id": 12},
        ],
    }
    monkeypatch.setattr(cmd_search, "_request_from_daemon", lambda q, limit, mode: fake)
    monkeypatch.setattr("feagle.picker.apps.discover_apps", lambda: [])
    monkeypatch.setattr(sys, "stdin", io.StringIO("2\n"))
    opened = {}
    monkeypatch.setattr(
        "feagle.picker.open_result", lambda openers, path, **kw: opened.update(path=path, **kw)
    )
    assert main(["search", "semantic", "--pick"]) == 0
    assert opened["path"] == target
    assert opened["line"] == 9  # not the first chunk (line 3)


def test_pick_query_uses_the_daemon(env, capsys, monkeypatch):
    docs = make_docs(env)
    write_config(env, '[launcher]\nprogram = "stdout"\n')
    main(["index", "--root", str(docs), "--no-embed"])
    capsys.readouterr()

    import io
    import sys

    calls = []

    def fake_daemon(query, limit, mode):
        calls.append(query)
        return {
            "ok": True,
            "mode": "hybrid",
            "took_ms": 1.0,
            "results": [
                {"rank": 1, "score": 1.0, "path": str(docs / "notes.txt"), "page": None,
                 "line": 3, "snippet": "semantic", "chunk_id": 1},
            ],
        }

    monkeypatch.setattr(cmd_search, "_request_from_daemon", fake_daemon)
    monkeypatch.setattr(
        cmd_search, "search_in_process",
        lambda *a, **k: pytest.fail("pick --query must use the daemon when it is up"),
    )
    monkeypatch.setattr("feagle.picker.apps.discover_apps", lambda: [])
    monkeypatch.setattr(sys, "stdin", io.StringIO("1\n"))
    opened = {}
    monkeypatch.setattr(
        "feagle.picker.open_result", lambda openers, path, **kw: opened.update(path=path, **kw)
    )
    assert main(["pick", "--query", "semantic"]) == 0
    assert calls == ["semantic"]
    assert opened["path"].name == "notes.txt"


def test_indexerd_wires_reconnect_reset_and_idle_hook(env, tmp_path, monkeypatch):
    import argparse

    from feagle.cli import cmd_indexerd

    captured = {}

    class FakeSupervisor:
        def stop_if_idle(self):
            captured["idle"] = True

    class FakeService:
        def __init__(self, cfg):
            self.cfg = cfg
            self.supervisor = FakeSupervisor()

        def start(self):
            raise ServerUnavailable("server down")

        def ensure(self):
            return "embedder"

        def reset(self):
            captured["reset"] = True

        def close(self, **kwargs):
            captured["closed"] = True

    class FakeDaemon:
        def __init__(self, cfg, **kwargs):
            captured["kwargs"] = kwargs

        def run_once(self):
            return pipeline.IndexStats()

        def run(self):
            captured["ran"] = True

    monkeypatch.setattr(cmd_indexerd, "EmbeddingService", FakeService)
    monkeypatch.setattr(cmd_indexerd, "IndexerDaemon", FakeDaemon)

    args = argparse.Namespace(root=[str(tmp_path / "missing")], poll=False, once=True)
    assert cmd_indexerd.run(args, config.default_config()) == 0

    kwargs = captured["kwargs"]
    assert kwargs["embedder_provider"]() == "embedder"
    assert kwargs["on_embedder_failure"] is not None
    assert kwargs["idle_hook"] is not None
    kwargs["idle_hook"]()
    assert captured["idle"] is True
    assert captured["reset"] is True  # startup failure drops the broken embedder
