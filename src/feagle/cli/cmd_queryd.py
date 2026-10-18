"""Run the query daemon (normally started by a systemd user unit)."""

from __future__ import annotations

import signal
import sys
from typing import TYPE_CHECKING, Any

from ..embeddings.client import EmbeddingError, ServerUnavailable
from ..embeddings.service import EmbeddingService, ModelMissingError
from ..search.queryd import QuerydServer, QueryEngine

if TYPE_CHECKING:
    import argparse

_DEGRADE_ERRORS = (ModelMissingError, ServerUnavailable, EmbeddingError, OSError)


def _raise_keyboard_interrupt(signum: int, frame: Any) -> None:
    raise KeyboardInterrupt


def run(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    service = EmbeddingService(cfg)
    embedder = None
    try:
        embedder = service.start()
    except _DEGRADE_ERRORS as exc:
        print(
            f"feagle: queryd starting without embeddings ({exc}); "
            "semantic search will be retried on demand",
            file=sys.stderr,
        )

    engine = QueryEngine(
        cfg,
        embedder=embedder,
        embedder_provider=service.ensure,
        on_embedder_failure=service.reset,
    )
    server = QuerydServer(
        engine,
        request_hook=service.touch,
        idle_hook=service.supervisor.stop_if_idle,
    )
    print(f"feagle: queryd listening on {server.socket_path}", file=sys.stderr)

    # systemd stops units with SIGTERM; interrupting the serve loop lets the
    # ``finally`` block stop an owned llama-server instead of orphaning it.
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    signal.signal(signal.SIGINT, _raise_keyboard_interrupt)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        service.close()
    return 0
