"""High-level embedding service: model resolution, supervision and client.

Ties the pieces together so callers (CLI, indexer, query daemon) get a ready
embedder with a known dimension, and so the server's lifetime is managed in one
place.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .. import paths
from ..log import get_logger
from .client import EmbeddingClient
from .presets import ModelSpec, spec_for
from .supervisor import LlamaSupervisor, SupervisorConfig

log = get_logger("embeddings.service")


class ModelMissingError(Exception):
    """The configured model file does not exist on disk."""


class Embedder(Protocol):
    """What the indexer and query daemon need from an embedder."""

    dim: int

    def embed_documents(self, items: list[tuple[str, str | None]]) -> list[list[float]]: ...

    def embed_queries(self, texts: list[str]) -> list[list[float]]: ...


@dataclass
class EmbedderAdapter:
    """Adapts :class:`EmbeddingClient` to the :class:`Embedder` protocol."""

    client: EmbeddingClient
    dim: int

    def embed_documents(self, items: list[tuple[str, str | None]]) -> list[list[float]]:
        return self.client.embed_documents(items)

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        return self.client.embed_queries(texts)


def resolve_model(cfg: dict[str, Any]) -> tuple[Path, ModelSpec | None]:
    """Return the local model path and its preset (if the config names one)."""
    name = cfg["embeddings"]["model"]
    spec = spec_for(name)
    if spec is not None:
        return paths.models_dir() / spec.filename, spec
    return Path(name).expanduser(), None


def build_supervisor(
    cfg: dict[str, Any],
    *,
    health: Callable[[str], bool] | None = None,
    spawn: Callable[[list[str]], Any] | None = None,
) -> LlamaSupervisor:
    embeddings = cfg["embeddings"]
    model_path, _spec = resolve_model(cfg)
    supervisor_cfg = SupervisorConfig(
        model_path=model_path,
        server_url=embeddings["server_url"],
        idle_timeout_min=embeddings["idle_timeout_min"],
        threads=embeddings["threads"],
        extra_args=list(embeddings["extra_args"]),
    )
    return LlamaSupervisor(supervisor_cfg, health=health, spawn=spawn)


class EmbeddingService:
    """Owns a supervisor and yields a client with a discovered dimension."""

    def __init__(
        self,
        cfg: dict[str, Any],
        *,
        health: Callable[[str], bool] | None = None,
        spawn: Callable[[list[str]], Any] | None = None,
        transport: Any | None = None,
    ) -> None:
        self.cfg = cfg
        self.supervisor = build_supervisor(cfg, health=health, spawn=spawn)
        self._transport = transport
        self._client: EmbeddingClient | None = None
        self._adapter: EmbedderAdapter | None = None

    def start(self) -> EmbedderAdapter:
        """Ensure a server is running and return a ready embedder."""
        embeddings = self.cfg["embeddings"]
        model_path, _spec = resolve_model(self.cfg)
        if not embeddings["server_url"] and not model_path.exists():
            raise ModelMissingError(
                f"model file not found: {model_path}\n"
                "run 'feagle setup' to download it, or set [embeddings].model "
                "to an existing GGUF path"
            )

        base_url = self.supervisor.ensure_running()
        self._client = EmbeddingClient(
            base_url,
            mrl_dim=embeddings["mrl_dim"],
            batch_size=self.cfg["index"]["embed_batch_size"],
            transport=self._transport,
        )
        dim = self._client.discover_dim()
        self._adapter = EmbedderAdapter(client=self._client, dim=dim)
        log.info("embeddings ready: dim=%d at %s", dim, base_url)
        return self._adapter

    @property
    def dim(self) -> int | None:
        return self._adapter.dim if self._adapter else None

    def touch(self) -> None:
        self.supervisor.touch()

    def close(self, *, stop_owned: bool = True) -> None:
        if self._client is not None:
            self._client.close()
        if stop_owned and self.supervisor.owned:
            self.supervisor.stop()
