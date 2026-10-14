"""Run the query daemon (normally started by a systemd user unit)."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from ..embeddings.client import EmbeddingError, ServerUnavailable
from ..embeddings.service import EmbeddingService, ModelMissingError
from ..search.queryd import QuerydServer, QueryEngine

_DEGRADE_ERRORS = (ModelMissingError, ServerUnavailable, EmbeddingError, OSError)


def run(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    service = EmbeddingService(cfg)
    embedder = None
    try:
        embedder = service.start()
    except _DEGRADE_ERRORS as exc:
        print(
            f"feagle: queryd starting without embeddings ({exc})",
            file=sys.stderr,
        )

    engine = QueryEngine(cfg, embedder=embedder)
    server = QuerydServer(
        engine,
        request_hook=service.touch,
        idle_hook=service.supervisor.stop_if_idle,
    )
    print(f"feagle: queryd listening on {server.socket_path}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        service.close()
    return 0
