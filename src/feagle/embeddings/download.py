"""Model download with integrity verification.

Downloads stream to a ``.part`` file, hashing as they go, then verify the size
and SHA256 against the pinned values before renaming into place. Optionally the
pin itself is re-checked against the Hugging Face tree API (``lfs.oid``), so a
re-uploaded file cannot silently invalidate our pin.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import httpx

from ..log import get_logger
from .presets import ModelSpec

log = get_logger("embeddings.download")

HF_API = "https://huggingface.co/api/models"
CHUNK_SIZE = 1 << 20  # 1 MiB

Progress = Callable[[int, int | None], None]


class DownloadError(Exception):
    """The download or its verification failed."""


def sha256_file(path: Path, *, chunk_size: int = CHUNK_SIZE) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def download_file(
    url: str,
    dest: Path,
    *,
    sha256: str | None = None,
    size: int | None = None,
    client: httpx.Client | None = None,
    chunk_size: int = CHUNK_SIZE,
    progress: Progress | None = None,
) -> Path:
    """Stream ``url`` to ``dest``, verifying size and hash before committing."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    owned = client is None
    client = client or httpx.Client(follow_redirects=True, timeout=60.0)
    digest = hashlib.sha256()
    total = 0
    try:
        try:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                expected = size if size is not None else _content_length(response)
                with open(partial, "wb") as fh:
                    for block in response.iter_bytes(chunk_size):
                        fh.write(block)
                        digest.update(block)
                        total += len(block)
                        if progress is not None:
                            progress(total, expected)
        except httpx.HTTPError as exc:
            partial.unlink(missing_ok=True)
            raise DownloadError(f"download failed: {exc}") from exc

        if size is not None and total != size:
            partial.unlink(missing_ok=True)
            raise DownloadError(f"size mismatch for {dest.name}: got {total}, expected {size}")
        if sha256 is not None and digest.hexdigest() != sha256:
            partial.unlink(missing_ok=True)
            raise DownloadError(f"sha256 mismatch for {dest.name}: got {digest.hexdigest()}")

        partial.replace(dest)
        return dest
    finally:
        if owned:
            client.close()


def _content_length(response: httpx.Response) -> int | None:
    header = response.headers.get("content-length")
    try:
        return int(header) if header is not None else None
    except ValueError:
        return None


def live_sha256(spec: ModelSpec, *, client: httpx.Client | None = None) -> str | None:
    """Return the file's ``lfs.oid`` from the HF tree API (``None`` if absent)."""
    owned = client is None
    client = client or httpx.Client(follow_redirects=True, timeout=30.0)
    try:
        response = client.get(f"{HF_API}/{spec.repo}/tree/main")
        response.raise_for_status()
        entries = response.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise DownloadError(f"cannot verify pin against Hugging Face: {exc}") from exc
    finally:
        if owned:
            client.close()

    for entry in entries:
        if entry.get("path") == spec.filename:
            return (entry.get("lfs") or {}).get("oid")
    return None


def verify_pin(spec: ModelSpec, *, client: httpx.Client | None = None) -> None:
    """Raise :class:`DownloadError` if the live pin differs from the preset."""
    remote = live_sha256(spec, client=client)
    if remote is not None and remote != spec.sha256:
        raise DownloadError(
            f"pinned sha256 for {spec.filename} is stale: "
            f"local pin {spec.sha256}, Hugging Face {remote}"
        )


def ensure_model(
    spec: ModelSpec,
    dest_dir: Path,
    *,
    client: httpx.Client | None = None,
    chunk_size: int = CHUNK_SIZE,
    progress: Progress | None = None,
) -> Path:
    """Return a local, verified copy of the model, downloading it if needed."""
    dest = dest_dir / spec.filename
    if dest.exists():
        if dest.stat().st_size == spec.size and sha256_file(dest) == spec.sha256:
            log.debug("model already present and verified: %s", dest)
            return dest
        log.warning("existing file failed verification, re-downloading: %s", dest)

    log.info("downloading %s (%d bytes)", spec.filename, spec.size)
    return download_file(
        spec.url,
        dest,
        sha256=spec.sha256,
        size=spec.size,
        client=client,
        chunk_size=chunk_size,
        progress=progress,
    )
