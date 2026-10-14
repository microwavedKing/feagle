"""The long-running indexer: initial pass, watch loop and periodic rescan.

It runs an initial incremental pass, then re-scans only the affected configured
roots whenever ``watchfiles`` reports changes, and does a full stat-based rescan
every ``rescan_interval_min``. Indexing is skipped (and retried sooner) while the
battery/load pause condition holds.
"""

from __future__ import annotations

import os
import queue
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from .. import db
from ..log import get_logger
from ..power import pause_reason
from . import pipeline
from .watcher import Watcher

log = get_logger("indexer.daemon")

QUEUE_TIMEOUT_S = 5.0
PAUSED_RETRY_S = 300.0


class IndexerDaemon:
    def __init__(
        self,
        cfg: dict[str, Any],
        *,
        conn: Any | None = None,
        embedder_provider: Callable[[], Any] | None = None,
        watcher: Watcher | None = None,
        stop_event: threading.Event | None = None,
        pause_check: Callable[[], str | None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        rescan_interval_s: float | None = None,
        watch: bool = True,
    ) -> None:
        self.cfg = cfg
        self.conn = conn
        self.watcher = watcher
        self.watch = watch
        self.stop_event = stop_event or threading.Event()
        self._embedder_provider = embedder_provider or (lambda: None)
        self.pause_check = pause_check or (lambda: pause_reason(self.cfg))
        self.clock = clock
        self.sleep = sleep
        if rescan_interval_s is None:
            rescan_interval_s = float(cfg["index"]["rescan_interval_min"]) * 60
        self.rescan_interval_s = rescan_interval_s

    # -- helpers -----------------------------------------------------------

    def _roots(self) -> list[str]:
        return list(self.cfg["index"]["roots"])

    def _absolute_roots(self) -> list[Path]:
        return [
            Path(os.path.abspath(os.path.expanduser(str(root)))) for root in self._roots()
        ]

    def roots_for(self, paths: Iterable[Path]) -> list[str] | None:
        """Configured roots that contain any of ``paths`` (or ``None``)."""
        roots = self._absolute_roots()
        selected: set[str] = set()
        for path in paths:
            resolved = Path(os.path.abspath(str(path)))
            for root in roots:
                if resolved == root or root in resolved.parents:
                    selected.add(str(root))
                    break
        return sorted(selected) if selected else None

    def _ensure_conn(self) -> Any:
        if self.conn is None:
            self.conn = db.open_database()
        return self.conn

    # -- indexing ----------------------------------------------------------

    def index_once(self, roots: list[str] | None = None) -> pipeline.IndexStats:
        conn = self._ensure_conn()
        embedder = self._embedder_provider()
        return pipeline.index_roots(
            conn, self.cfg, roots=roots, embedder=embedder, pause_check=self.pause_check
        )

    def run_once(self) -> pipeline.IndexStats:
        """One pass over all roots, honouring the pause condition upfront."""
        if not self._roots():
            log.warning("no index roots configured; nothing to index")
            return pipeline.IndexStats()
        reason = self.pause_check()
        if reason:
            log.info("indexing paused: %s", reason)
            return pipeline.IndexStats(paused=reason)
        return self.index_once()

    # -- daemon loop -------------------------------------------------------

    def run(self) -> None:
        if not self._roots():
            log.warning("no index roots configured; indexerd has nothing to do")
            return

        log.info("initial scan")
        stats = self.run_once()
        next_rescan = self._next_rescan(stats)

        events: queue.Queue[set[Path] | None] | None = None
        if self.watch:
            if self.watcher is None:
                self.watcher = Watcher(self._roots())
            events = queue.Queue()
            threading.Thread(
                target=self._produce, args=(events,), name="feagle-watch", daemon=True
            ).start()

        while not self.stop_event.is_set():
            now = self.clock()
            if self.rescan_interval_s and now >= next_rescan:
                log.info("periodic rescan")
                next_rescan = self._next_rescan(self.run_once())
                continue

            timeout = QUEUE_TIMEOUT_S
            if self.rescan_interval_s:
                timeout = max(0.1, min(timeout, next_rescan - now))

            if events is None:
                self.sleep(timeout)
                continue

            try:
                changed = events.get(timeout=timeout)
            except queue.Empty:
                continue

            if changed is None:  # watcher finished
                break
            roots = self.roots_for(changed)
            if roots is None:
                continue
            log.info("change in %d path(s); rescanning %s", len(changed), roots)
            self.index_once(roots=roots)

    def _next_rescan(self, stats: pipeline.IndexStats) -> float:
        now = self.clock()
        if stats.paused:
            return now + PAUSED_RETRY_S
        if self.rescan_interval_s:
            return now + self.rescan_interval_s
        return now

    def _produce(self, events: queue.Queue[set[Path] | None]) -> None:
        try:
            for paths in self.watcher.events(self.stop_event):
                if paths:
                    events.put(paths)
                if self.stop_event.is_set():
                    break
        except Exception:  # noqa: BLE001 - a broken watcher must not kill the daemon
            log.exception("watcher failed")
        finally:
            events.put(None)
