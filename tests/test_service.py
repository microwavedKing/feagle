"""Tests for the embedding service glue."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from feagle import config, paths
from feagle.embeddings import service
from feagle.embeddings.client import ServerUnavailable


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return tmp_path


def cfg_with(**embeddings):
    cfg = config.default_config()
    cfg["embeddings"].update(embeddings)
    return cfg


def echo_transport(dim=5):
    def handler(request):
        inputs = json.loads(request.content)["input"]
        data = [{"embedding": [1.0] * dim, "index": i} for i in range(len(inputs))]
        return httpx.Response(200, json={"data": data})

    return httpx.MockTransport(handler)


def test_resolve_model_preset(env):
    path, spec = service.resolve_model(cfg_with(model="embeddinggemma-2-q8"))
    assert spec is not None
    assert path == paths.models_dir() / "embeddinggemma-2-Q8_0.gguf"


def test_resolve_model_explicit_path(env):
    path, spec = service.resolve_model(cfg_with(model="~/some/model.gguf"))
    assert spec is None
    assert path == Path("~/some/model.gguf").expanduser()


def test_missing_model_raises(env):
    svc = service.EmbeddingService(cfg_with(model="embeddinggemma-2-q8"))
    with pytest.raises(service.ModelMissingError, match="feagle setup"):
        svc.start()


def test_start_with_external_server(env):
    svc = service.EmbeddingService(
        cfg_with(model="embeddinggemma-2-q8", server_url="http://127.0.0.1:9"),
        transport=echo_transport(dim=5),
    )
    embedder = svc.start()
    assert embedder.dim == 5
    assert len(embedder.embed_queries(["hello world"])[0]) == 5
    svc.close()
    assert svc.supervisor.owned is False


def test_start_retries_a_transient_probe_failure(env, monkeypatch):
    attempts = {"n": 0}

    def flaky(self):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ServerUnavailable("server went away")
        return 5

    monkeypatch.setattr(service.EmbeddingClient, "discover_dim", flaky)
    monkeypatch.setattr(service.time, "sleep", lambda seconds: None)

    svc = service.EmbeddingService(
        cfg_with(model="embeddinggemma-2-q8", server_url="http://127.0.0.1:9"),
        transport=echo_transport(dim=5),
    )
    embedder = svc.start()
    assert embedder.dim == 5
    assert attempts["n"] == 2
    svc.close()


def test_reset_drops_the_cached_embedder(env):
    svc = service.EmbeddingService(
        cfg_with(model="embeddinggemma-2-q8", server_url="http://127.0.0.1:9"),
        transport=echo_transport(dim=5),
    )
    first = svc.ensure()
    assert svc.ensure() is first  # cached
    svc.reset()
    assert svc.dim is None


def test_embedder_refreshes_the_activity_stamp(env):
    touched = []
    svc = service.EmbeddingService(
        cfg_with(model="embeddinggemma-2-q8", server_url="http://127.0.0.1:9"),
        transport=echo_transport(dim=5),
    )
    embedder = svc.start()
    embedder.touch = lambda: touched.append(1)
    embedder.embed_queries(["hello world"])
    embedder.embed_documents([("body", "title")])
    assert touched == [1, 1]


def test_close_does_not_stop_external(env):
    svc = service.EmbeddingService(
        cfg_with(model="embeddinggemma-2-q8", server_url="http://127.0.0.1:9"),
        transport=echo_transport(),
    )
    svc.start()
    svc.close()
    assert svc.supervisor.owned is False


def test_build_supervisor_carries_settings(env):
    cfg = cfg_with(server_url="http://ext:8080", idle_timeout_min=3, threads=2, extra_args=["-x"])
    sup = service.build_supervisor(cfg)
    assert sup.cfg.server_url == "http://ext:8080"
    assert sup.cfg.idle_timeout_min == 3
    assert sup.cfg.threads == 2
    assert sup.cfg.extra_args == ["-x"]


def test_build_supervisor_can_isolate_its_runtime_files(env, tmp_path):
    sup = service.build_supervisor(cfg_with(model="embeddinggemma-2-q8"), runtime_dir=tmp_path)
    assert sup.port_file == tmp_path / "llama.port"
    assert sup.pid_file == tmp_path / "llama.pid"
    assert sup.lock_file == tmp_path / "llama.lock"
    assert sup.touch_file == tmp_path / "llama.touch"


# -- media / mmproj --------------------------------------------------------


def test_resolve_mmproj_none_when_images_disabled(env):
    assert service.resolve_mmproj(cfg_with()) is None


def test_resolve_mmproj_preset(env):
    cfg = cfg_with()
    cfg["media"]["images"] = True
    assert service.resolve_mmproj(cfg) == paths.models_dir() / "mmproj-embeddinggemma-2-Q8_0.gguf"


def test_resolve_mmproj_explicit_path(env):
    cfg = cfg_with()
    cfg["media"].update(images=True, mmproj="~/some/mm.gguf")
    assert service.resolve_mmproj(cfg) == Path("~/some/mm.gguf").expanduser()


def test_build_supervisor_carries_mmproj_only_when_present(env):
    cfg = cfg_with(model="embeddinggemma-2-q8")
    cfg["media"]["images"] = True
    assert service.build_supervisor(cfg).cfg.mmproj_path is None  # file is missing
    target = paths.models_dir() / "mmproj-embeddinggemma-2-Q8_0.gguf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"x")
    assert service.build_supervisor(cfg).cfg.mmproj_path == target


def test_start_requires_mmproj_when_images_enabled(env):
    cfg = cfg_with(model="embeddinggemma-2-q8")
    cfg["media"]["images"] = True
    model = paths.models_dir() / "embeddinggemma-2-Q8_0.gguf"
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"x")
    with pytest.raises(service.ModelMissingError, match="multimodal projector"):
        service.EmbeddingService(cfg).start()


def test_embedder_adapter_embeds_images(env, tmp_path):
    touched = []
    svc = service.EmbeddingService(
        cfg_with(model="embeddinggemma-2-q8", server_url="http://127.0.0.1:9"),
        transport=echo_transport(dim=5),
    )
    embedder = svc.start()
    embedder.touch = lambda: touched.append(1)
    image = tmp_path / "a.png"
    image.write_bytes(b"PNG")
    assert len(embedder.embed_images([image])[0]) == 5
    assert touched == [1]
    svc.close()
