"""Run the indexing daemon (normally started by a systemd user unit)."""

from __future__ import annotations

import argparse
import signal
import sys
import threading
from pathlib import Path
from typing import Any

from ..embeddings.client import EmbeddingError, ServerUnavailable
from ..embeddings.service import EmbeddingService, ModelMissingError, media_enabled
from ..indexer.daemon import IndexerDaemon
from ..indexer.watcher import Watcher

_DEGRADE_ERRORS = (ModelMissingError, ServerUnavailable, EmbeddingError, OSError)


def _raise_keyboard_interrupt(signum: int, frame: Any) -> None:
    raise KeyboardInterrupt


def run(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    roots = args.root or cfg["index"]["roots"]
    if not roots:
        print(
            "feagle: no index roots configured; run 'feagle setup' "
            "or set [index].roots in the config.",
            file=sys.stderr,
        )
        return 1

    service = EmbeddingService(cfg, media=media_enabled(cfg))
    try:
        service.start()
    except _DEGRADE_ERRORS as exc:
        print(
            f"feagle: indexerd starting without embeddings ({exc}); "
            "semantic indexing will be retried on demand",
            file=sys.stderr,
        )
        service.reset()

    stop_event = threading.Event()
    watch_roots = [root for root in roots if Path(root).expanduser().is_dir()]
    watcher = Watcher(watch_roots, poll=args.poll) if watch_roots else None
    daemon = IndexerDaemon(
        cfg,
        embedder_provider=service.ensure,
        on_embedder_failure=service.reset,
        idle_hook=service.supervisor.stop_if_idle,
        watcher=watcher,
        stop_event=stop_event,
        watch=bool(watch_roots),
    )

    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    signal.signal(signal.SIGINT, _raise_keyboard_interrupt)
    print(f"feagle: indexerd watching {len(roots)} root(s)", file=sys.stderr)
    try:
        if args.once:
            stats = daemon.run_once()
            print(stats.summary())
        else:
            daemon.run()
    except KeyboardInterrupt:
        stop_event.set()
    finally:
        service.close()
    return 0
