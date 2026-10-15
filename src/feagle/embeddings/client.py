"""HTTP client for ``llama-server``'s OpenAI-compatible embeddings endpoint.

Responsibilities:

- ``GET /health`` readiness probing;
- batched ``POST /v1/embeddings`` with ``input`` as a list;
- validating that every input comes back exactly once (ordered by ``index``);
- discovering the output dimension from a live request (never hardcoded);
- applying Matryoshka (MRL) truncation with mandatory L2 re-normalization.
"""

from __future__ import annotations

import base64
import math
from pathlib import Path

import httpx

from . import prompts

#: Placeholder title used when a document has no name.
DEFAULT_BATCH_SIZE = 16

#: Images are heavier than text, so they are sent in smaller batches.
IMAGE_BATCH_SIZE = 4

#: MIME types for the image extensions we index.
_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
}


def _data_url(path: Path) -> str:
    """A ``data:`` URL with the base64-encoded image bytes."""
    mime = _IMAGE_MIME.get(path.suffix.lower(), "application/octet-stream")
    try:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError as exc:
        raise EmbeddingError(f"cannot read image {path}: {exc}") from exc
    return f"data:{mime};base64,{encoded}"


class EmbeddingError(Exception):
    """The embeddings server returned something unusable."""


class ServerUnavailable(EmbeddingError):
    """The embeddings server could not be reached."""


def _l2_normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(component * component for component in vector))
    if norm == 0.0:
        return list(vector)
    return [component / norm for component in vector]


class EmbeddingClient:
    """A thin, testable client around ``llama-server``."""

    def __init__(
        self,
        base_url: str,
        *,
        mrl_dim: int | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        task: str = prompts.DEFAULT_TASK,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.mrl_dim = mrl_dim
        self.batch_size = max(1, batch_size)
        self.task = task
        kwargs: dict[str, object] = {"timeout": timeout}
        if transport is not None:
            kwargs["transport"] = transport
        self._client = httpx.Client(**kwargs)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> EmbeddingClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- lifecycle ---------------------------------------------------------

    def health(self, timeout: float = 2.0) -> bool:
        """Return ``True`` when the server reports ready."""
        try:
            response = self._client.get(f"{self.base_url}/health", timeout=timeout)
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    # -- embedding ---------------------------------------------------------

    def _apply_mrl(self, vector: list[float]) -> list[float]:
        if self.mrl_dim is None or self.mrl_dim >= len(vector):
            return vector
        return _l2_normalize(vector[: self.mrl_dim])

    def _embed_raw(self, inputs: list[str]) -> list[list[float]]:
        if not inputs:
            return []
        if any(not text.strip() for text in inputs):
            raise EmbeddingError("refusing to embed an empty input")

        vectors: list[list[float] | None] = [None] * len(inputs)
        for start in range(0, len(inputs), self.batch_size):
            batch = inputs[start : start + self.batch_size]
            try:
                response = self._client.post(
                    f"{self.base_url}/v1/embeddings",
                    json={"input": batch, "model": "feagle"},
                )
            except httpx.HTTPError as exc:
                raise ServerUnavailable(f"embeddings request failed: {exc}") from exc
            if response.status_code != 200:
                raise EmbeddingError(
                    f"embeddings HTTP {response.status_code}: {response.text[:200]}"
                )

            try:
                payload = response.json()
                data = payload["data"]
            except (ValueError, KeyError, TypeError) as exc:
                raise EmbeddingError(f"malformed embeddings response: {exc}") from exc

            if len(data) != len(batch):
                raise EmbeddingError(
                    f"expected {len(batch)} embeddings, got {len(data)}"
                )
            for item in data:
                index = int(item.get("index", 0))
                if not 0 <= index < len(batch):
                    raise EmbeddingError(f"embedding index {index} out of range")
                vectors[start + index] = list(item["embedding"])

        if any(vector is None for vector in vectors):
            raise EmbeddingError("server did not return every embedding")
        return [self._apply_mrl(vector) for vector in vectors]  # type: ignore[arg-type]

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        for text in texts:
            if not text.strip():
                raise EmbeddingError("refusing to embed an empty input")
        return self._embed_raw([prompts.format_query(text, self.task) for text in texts])

    def embed_documents(self, items: list[tuple[str, str | None]]) -> list[list[float]]:
        """Embed ``(text, title)`` pairs with the document prompt."""
        for text, _title in items:
            if not text.strip():
                raise EmbeddingError("refusing to embed an empty input")
        return self._embed_raw(
            [prompts.format_doc(text, title, self.task) for text, title in items]
        )

    def embed_images(self, paths: list[Path]) -> list[list[float]]:
        """Embed image files through the multimodal projector.

        Media inputs take **no** task prefix (see RESEARCH §4.2); the server
        inserts the ``<|image|>`` placeholder itself. The request uses the
        content-parts form of ``/v1/embeddings``.
        """
        if not paths:
            return []
        vectors: list[list[float] | None] = [None] * len(paths)
        batch_size = max(1, min(self.batch_size, IMAGE_BATCH_SIZE))
        for start in range(0, len(paths), batch_size):
            batch = paths[start : start + batch_size]
            inputs = [
                {"content": [{"type": "image_url", "image_url": {"url": _data_url(path)}}]}
                for path in batch
            ]
            try:
                response = self._client.post(
                    f"{self.base_url}/v1/embeddings",
                    json={"input": inputs, "model": "feagle"},
                )
            except httpx.HTTPError as exc:
                raise ServerUnavailable(f"embeddings request failed: {exc}") from exc
            if response.status_code != 200:
                raise EmbeddingError(
                    f"embeddings HTTP {response.status_code}: {response.text[:200]}"
                )
            try:
                data = response.json()["data"]
            except (ValueError, KeyError, TypeError) as exc:
                raise EmbeddingError(f"malformed embeddings response: {exc}") from exc
            if len(data) != len(batch):
                raise EmbeddingError(f"expected {len(batch)} embeddings, got {len(data)}")
            for item in data:
                index = int(item.get("index", 0))
                if not 0 <= index < len(batch):
                    raise EmbeddingError(f"embedding index {index} out of range")
                vectors[start + index] = list(item["embedding"])

        if any(vector is None for vector in vectors):
            raise EmbeddingError("server did not return every embedding")
        return [self._apply_mrl(vector) for vector in vectors]  # type: ignore[arg-type]

    def discover_dim(self) -> int:
        """Probe the server once to learn the (possibly MRL-truncated) dimension."""
        vectors = self._embed_raw(["dimension probe"])
        if not vectors:
            raise EmbeddingError("server returned no vector for the dimension probe")
        return len(vectors[0])
