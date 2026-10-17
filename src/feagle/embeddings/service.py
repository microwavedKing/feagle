"""High-level embedding service: model resolution, supervision and client.

Ties the pieces together so callers (CLI, indexer, query daemon) get a ready
embedder with a known dimension, and so the server's lifetime is managed in one
place.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .. import paths
from ..log import get_logger
from . import prompts
from .client import EmbeddingClient, EmbeddingError, ServerUnavailable
from .presets import MMPROJ_PRESETS, ModelSpec, spec_for
from .supervisor import LlamaSupervisor, SupervisorConfig

log = get_logger("embeddings.service")

#: ``start()`` retries the first probe a few times before degrading.
_START_ATTEMPTS = 3
_START_RETRY_S = 1.0


class ModelMissingError(Exception):
    """The configured model file does not exist on disk."""


class Embedder(Protocol):
    """What the indexer and query daemon need from an embedder."""

    dim: int

    def embed_documents(self, items: list[tuple[str, str | None]]) -> list[list[float]]: ...

    def embed_queries(self, texts: list[str]) -> list[list[float]]: ...

    def embed_images(self, paths: list[Path]) -> list[list[float]]: ...


@dataclass
class EmbedderAdapter:
    """Adapts :class:`EmbeddingClient` to the :class:`Embedder` protocol.

    Every successful call refreshes the shared activity stamp so that the
    server's owner does not unload it while it is still in use.
    """

    client: EmbeddingClient
    dim: int
    touch: Callable[[], None] | None = None

    def embed_documents(self, items: list[tuple[str, str | None]]) -> list[list[float]]:
        vectors = self.client.embed_documents(items)
        if self.touch is not None:
            self.touch()
        return vectors

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        vectors = self.client.embed_queries(texts)
        if self.touch is not None:
            self.touch()
        return vectors

    def embed_images(self, paths: list[Path]) -> list[list[float]]:
        vectors = self.client.embed_images(paths)
        if self.touch is not None:
            self.touch()
        return vectors


def resolve_model(cfg: dict[str, Any]) -> tuple[Path, ModelSpec | None]:
    """Return the local model path and its preset (if the config names one)."""
    name = cfg["embeddings"]["model"]
    spec = spec_for(name)
    if spec is not None:
        return paths.models_dir() / spec.filename, spec
    return Path(name).expanduser(), None


def resolve_mmproj(cfg: dict[str, Any]) -> Path | None:
    """Return the multimodal projector path when image/video indexing is enabled."""
    media = cfg.get("media") or {}
    if not (media.get("images") or media.get("video")):
        return None
    name = media.get("mmproj") or "embeddinggemma-2-mmproj-q8"
    spec = MMPROJ_PRESETS.get(name)
    if spec is not None:
        return paths.models_dir() / spec.filename
    return Path(name).expanduser()


def media_enabled(cfg: dict[str, Any]) -> bool:
    """Whether the indexer needs a multimodal server (images/video, not audio)."""
    media = cfg.get("media") or {}
    return bool(media.get("images") or media.get("video"))


def build_supervisor(
    cfg: dict[str, Any],
    *,
    media: bool = False,
    health: Callable[[str], bool] | None = None,
    spawn: Callable[[list[str]], Any] | None = None,
    runtime_dir: Path | None = None,
) -> LlamaSupervisor:
    embeddings = cfg["embeddings"]
    model_path, spec = resolve_model(cfg)
    mmproj = resolve_mmproj(cfg) if media else None
    supervisor_cfg = SupervisorConfig(
        model_path=model_path,
        server_url=embeddings["server_url"],
        idle_timeout_min=embeddings["idle_timeout_min"],
        threads=embeddings["threads"],
        mmproj_path=mmproj if (mmproj is not None and mmproj.exists()) else None,
        media=media,
        extra_args=list(embeddings["extra_args"]),
        pooling=spec.pooling if spec is not None else None,
    )
    files: dict[str, Path] = {}
    if runtime_dir is not None:
        # Isolate from a server another process may already own (used by the
        # benchmarks, which must measure *this* model).
        runtime_dir = Path(runtime_dir)
        files = {
            "port_file": runtime_dir / "llama.port",
            "pid_file": runtime_dir / "llama.pid",
            "lock_file": runtime_dir / "llama.lock",
            "touch_file": runtime_dir / "llama.touch",
        }
    return LlamaSupervisor(supervisor_cfg, health=health, spawn=spawn, **files)


class EmbeddingService:
    """Owns a supervisor and yields a client with a discovered dimension."""

    def __init__(
        self,
        cfg: dict[str, Any],
        *,
        media: bool = False,
        health: Callable[[str], bool] | None = None,
        spawn: Callable[[list[str]], Any] | None = None,
        transport: Any | None = None,
        runtime_dir: Path | None = None,
    ) -> None:
        self.cfg = cfg
        self._media = media
        self.supervisor = build_supervisor(
            cfg, media=media, health=health, spawn=spawn, runtime_dir=runtime_dir
        )
        self._transport = transport
        self._client: EmbeddingClient | None = None
        self._adapter: EmbedderAdapter | None = None

    def start(self) -> EmbedderAdapter:
        """Ensure a server is running and return a ready embedder.

        The first probe can fail if the server we reused is being restarted by
        another process, so it is retried a couple of times before giving up.
        """
        embeddings = self.cfg["embeddings"]
        model_path, _spec = resolve_model(self.cfg)
        if not embeddings["server_url"] and not model_path.exists():
            raise ModelMissingError(
                f"model file not found: {model_path}\n"
                "run 'feagle setup' to download it, or set [embeddings].model "
                "to an existing GGUF path"
            )
        if self._media and not embeddings["server_url"]:
            mmproj = resolve_mmproj(self.cfg)
            if mmproj is None or not mmproj.exists():
                raise ModelMissingError(
                    f"multimodal projector not found: {mmproj}\n"
                    "download it or set [media].mmproj to an existing GGUF path"
                )

        base_url = self.supervisor.ensure_running()
        self._client = self._make_client(base_url)
        for attempt in range(1, _START_ATTEMPTS + 1):
            try:
                dim = self._client.discover_dim()
                break
            except (ServerUnavailable, EmbeddingError) as exc:
                if attempt == _START_ATTEMPTS:
                    raise
                log.warning("embeddings probe failed (%s); retrying", exc)
                time.sleep(_START_RETRY_S)
                self._client.close()
                base_url = self.supervisor.ensure_running()
                self._client = self._make_client(base_url)

        self._adapter = EmbedderAdapter(
            client=self._client, dim=dim, touch=self.supervisor.touch
        )
        log.info("embeddings ready: dim=%d at %s", dim, base_url)
        return self._adapter

    def _make_client(self, base_url: str) -> EmbeddingClient:
        _path, spec = resolve_model(self.cfg)
        return EmbeddingClient(
            base_url,
            mrl_dim=self.cfg["embeddings"]["mrl_dim"],
            batch_size=self.cfg["index"]["embed_batch_size"],
            family=spec.prompt_family if spec is not None else prompts.DEFAULT_FAMILY,
            transport=self._transport,
        )

    def ensure(self) -> EmbedderAdapter:
        """Return the embedder, starting it on first use (retried after a reset)."""
        if self._adapter is not None:
            return self._adapter
        return self.start()

    def reset(self) -> None:
        """Forget the current embedder so the next :meth:`ensure` restarts it."""
        if self._client is not None:
            self._client.close()
        self._client = None
        self._adapter = None

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
