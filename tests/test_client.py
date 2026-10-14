"""Tests for the embeddings HTTP client (no real server needed)."""

from __future__ import annotations

import json
import math

import httpx
import pytest

from feagle.embeddings.client import EmbeddingClient, EmbeddingError, ServerUnavailable


def make_client(handler, **overrides):
    transport = httpx.MockTransport(handler)
    params = {"mrl_dim": None, "batch_size": 16, "task": "search"}
    params.update(overrides)
    return EmbeddingClient("http://127.0.0.1:9", transport=transport, **params)


def echo_handler(dim=4, seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        body = json.loads(request.content)
        inputs = body["input"]
        if seen is not None:
            seen.append(inputs)
        data = [
            {"embedding": [float(i + 1)] * dim, "index": i} for i in range(len(inputs))
        ]
        return httpx.Response(200, json={"data": data})

    return handler


def test_health_ok():
    client = make_client(echo_handler())
    assert client.health() is True


def test_health_down():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    assert make_client(handler).health() is False


def test_health_not_ready():
    def handler(request):
        return httpx.Response(503)

    assert make_client(handler).health() is False


def test_query_prompt_is_applied():
    seen = []
    client = make_client(echo_handler(seen=seen))
    client.embed_queries(["hello"])
    assert seen == [["task: search result | query: hello"]]


def test_document_prompt_and_title():
    seen = []
    client = make_client(echo_handler(seen=seen))
    client.embed_documents([("body", "notes"), ("body2", None)])
    assert seen[0][0] == "title: notes | text: body"
    assert seen[0][1] == "title: none | text: body2"


def test_batching_splits_requests():
    seen = []
    client = make_client(echo_handler(seen=seen), batch_size=2)
    vectors = client.embed_queries(["a one", "b two", "c three", "d four", "e five"])
    assert len(vectors) == 5
    assert [len(batch) for batch in seen] == [2, 2, 1]


def test_results_are_reordered_by_index():
    def handler(request):
        if request.url.path == "/health":
            return httpx.Response(200)
        inputs = json.loads(request.content)["input"]
        data = [{"embedding": [float(i)], "index": i} for i in range(len(inputs))]
        data.reverse()  # server returns them out of order
        return httpx.Response(200, json={"data": data})

    client = make_client(handler)
    vectors = client.embed_queries(["first", "second", "third"])
    assert [v[0] for v in vectors] == [0.0, 1.0, 2.0]


def test_mrl_truncates_and_renormalizes():
    client = make_client(echo_handler(dim=4), mrl_dim=2)
    vector = client.embed_queries(["hello world"])[0]
    assert len(vector) == 2
    assert math.isclose(math.sqrt(sum(c * c for c in vector)), 1.0, rel_tol=1e-9)


def test_no_truncation_when_dim_matches():
    client = make_client(echo_handler(dim=4), mrl_dim=4)
    assert len(client.embed_queries(["hello world"])[0]) == 4


def test_count_mismatch_raises():
    def handler(request):
        if request.url.path == "/health":
            return httpx.Response(200)
        return httpx.Response(200, json={"data": [{"embedding": [1.0], "index": 0}]})

    client = make_client(handler)
    with pytest.raises(EmbeddingError, match="expected 2 embeddings, got 1"):
        client.embed_queries(["one here", "two here"])


def test_empty_input_raises():
    client = make_client(echo_handler())
    with pytest.raises(EmbeddingError, match="empty input"):
        client.embed_queries([""])


def test_http_error_raises_server_unavailable():
    def handler(request):
        if request.url.path == "/health":
            return httpx.Response(200)
        raise httpx.ConnectError("refused", request=request)

    client = make_client(handler)
    with pytest.raises(ServerUnavailable):
        client.embed_queries(["hello world"])


def test_non_200_raises_embedding_error():
    def handler(request):
        if request.url.path == "/health":
            return httpx.Response(200)
        return httpx.Response(500, text="boom")

    client = make_client(handler)
    with pytest.raises(EmbeddingError, match="HTTP 500"):
        client.embed_queries(["hello world"])


def test_discover_dim():
    client = make_client(echo_handler(dim=7))
    assert client.discover_dim() == 7
