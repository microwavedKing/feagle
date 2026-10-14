"""Tests for model download and verification."""

from __future__ import annotations

import hashlib

import httpx
import pytest

from feagle.embeddings import download, presets

BLOB = b"x" * 5000
BLOB_SHA = hashlib.sha256(BLOB).hexdigest()


def _spec(**overrides):
    base = {
        "name": "test",
        "filename": "test.gguf",
        "url": "https://hf.example/test.gguf",
        "sha256": BLOB_SHA,
        "size": len(BLOB),
        "dim": 8,
        "context": 128,
        "repo": "org/repo",
    }
    base.update(overrides)
    return presets.ModelSpec(**base)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_download_verifies_and_renames(tmp_path):
    def handler(request):
        return httpx.Response(200, content=BLOB, headers={"content-length": str(len(BLOB))})

    client = _client(handler)
    spec = _spec()
    dest = download.download_file(
        spec.url, tmp_path / "test.gguf", sha256=BLOB_SHA, size=len(BLOB), client=client
    )
    assert dest.read_bytes() == BLOB
    assert not (tmp_path / "test.gguf.part").exists()


def test_sha_mismatch_leaves_no_file(tmp_path):
    def handler(request):
        return httpx.Response(200, content=BLOB)

    client = _client(handler)
    with pytest.raises(download.DownloadError, match="sha256 mismatch"):
        download.download_file(
            "https://hf.example/x", tmp_path / "x.gguf", sha256="0" * 64, client=client
        )
    assert not (tmp_path / "x.gguf").exists()
    assert not (tmp_path / "x.gguf.part").exists()


def test_size_mismatch_raises(tmp_path):
    def handler(request):
        return httpx.Response(200, content=BLOB)

    client = _client(handler)
    with pytest.raises(download.DownloadError, match="size mismatch"):
        download.download_file("https://hf.example/x", tmp_path / "x.gguf", size=999, client=client)


def test_progress_is_reported(tmp_path):
    seen = []

    def handler(request):
        return httpx.Response(200, content=BLOB)

    client = _client(handler)
    download.download_file(
        "https://hf.example/x", tmp_path / "x.gguf", size=len(BLOB),
        client=client, chunk_size=1024, progress=lambda done, total: seen.append((done, total)),
    )
    assert seen[-1] == (len(BLOB), len(BLOB))


def test_ensure_model_reuses_verified_file(tmp_path):
    dest = tmp_path / "test.gguf"
    dest.write_bytes(BLOB)

    def handler(request):  # must not be called
        raise AssertionError("network used for a verified local file")

    result = download.ensure_model(_spec(), tmp_path, client=_client(handler))
    assert result == dest


def test_ensure_model_redownloads_on_mismatch(tmp_path):
    (tmp_path / "test.gguf").write_bytes(b"corrupt")

    def handler(request):
        return httpx.Response(200, content=BLOB)

    result = download.ensure_model(_spec(), tmp_path, client=_client(handler))
    assert result.read_bytes() == BLOB


def test_verify_pin_ok_and_stale():
    def tree(sha):
        def handler(request):
            return httpx.Response(200, json=[{"path": "test.gguf", "lfs": {"oid": sha}}])

        return handler

    download.verify_pin(_spec(), client=_client(tree(BLOB_SHA)))
    with pytest.raises(download.DownloadError, match="stale"):
        download.verify_pin(_spec(), client=_client(tree("a" * 64)))


def test_live_sha256_missing_file_returns_none():
    def handler(request):
        return httpx.Response(200, json=[{"path": "other.gguf"}])

    assert download.live_sha256(_spec(), client=_client(handler)) is None
