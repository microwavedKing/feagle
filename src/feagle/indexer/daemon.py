"""The long-running indexer: initial pass, watch loop and periodic rescan.

It runs an initial incremental pass, then re-scans only the affected configured
roots whenever ``watchfiles`` reports changes, and does a full stat-based rescan
every ``rescan_interval_min``. Indexing is skipped (and retried sooner) while the
battery/load pause condition holds.
"""

from __future__ import annotations

import contextlib
import os
import queue
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import db
from ..embeddings.client import ServerUnavailable
from ..log import get_logger
from ..power import pause_reason
from . import pipeline
from .watcher import Watcher

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

log = get_logger("indexer.daemon")

QUEUE_TIMEOUT_S = 5.0
PAUSED_RETRY_S = 300.0


class _ResilientEmbedder:
    """Re-creates the embedder when the server drops in the middle of a pass.

    ``llama-server`` may be unloaded for being idle (or crash) between two
    batches. Without this, the indexer would keep a dead client forever and
    write every following file with ``embedded=0``. On :class:`ServerUnavailable`
    we ask the owner to drop the broken embedder and fetch a fresh one, then
    retry the call once.
    """

    def __init__(
        self,
        embedder: Any,
        provider: Callable[[], Any],
        on_failure: Callable[[], Any],
    ) -> None:
        self._embedder = embedder
        self._provider = provider
        self._on_failure = on_failure

    @property
    def dim(self) -> int:
        return self._embedder.dim

    def _call(self, method: str, *args: Any) -> Any:
        try:
            return getattr(self._embedder, method)(*args)
        except ServerUnavailable as exc:
            log.warning("embeddings server unavailable (%s); restarting it", exc)
            with contextlib.suppress(Exception):
                self._on_failure()
            try:
                self._embedder = self._provider()
            except Exception as new_exc:
                log.warning("could not restart embeddings: %s", new_exc)
                raise ServerUnavailable(str(new_exc)) from new_exc
            return getattr(self._embedder, method)(*args)

    def embed_documents(self, items: list[tuple[str, str | None]]) -> list[list[float]]:
        return self._call("embed_documents", items)

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        return self._call("embed_queries", texts)

    def __getattr__(self, name: str) -> Any:
        """Forward anything else to the wrapped embedder, with the same retry.

        Capability checks like ``getattr(embedder, "embed_images", None)`` have to
        see through this wrapper (a text-only embedder genuinely lacks the
        method), and the call itself must still survive a dropped server.
        """
        embedder = self.__dict__.get("_embedder")
        if embedder is None:  # during __init__, before the wrapped object is set
            raise AttributeError(name)
        attribute = getattr(embedder, name)
        if not callable(attribute):
            return attribute

        def forward(*args: Any) -> Any:
            return self._call(name, *args)

        return forward



class IndexerDaemon:
    def __init__(
        self,
        cfg: dict[str, Any],
        *,
        conn: Any | None = None,
        embedder_provider: Callable[[], Any] | None = None,
        on_embedder_failure: Callable[[], Any] | None = None,
        idle_hook: Callable[[], Any] | None = None,
        watcher: Watcher | None = None,
        stop_event: threading.Event | None = None,
        pause_check: Callable[[], str | None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        rescan_interval_s: float | None = None,
        watch: bool = True,
        reload_config: Callable[[], dict[str, Any] | None] | None = None,
    ) -> None:
        self.cfg = cfg
        self.conn = conn
        self.watcher = watcher
        self.watch = watch
        self.stop_event = stop_event or threading.Event()
        self._embedder_provider = embedder_provider or (lambda: None)
        self._on_embedder_failure = on_embedder_failure
        self.idle_hook = idle_hook
        self.pause_check = pause_check or (lambda: pause_reason(self.cfg))
        self._pause_injected = pause_check is not None
        self._reload = reload_config
        self.clock = clock
        self.sleep = sleep
        self._rescan_override = rescan_interval_s
        if rescan_interval_s is None:
            rescan_interval_s = float(cfg["index"]["rescan_interval_min"]) * 60
        self.rescan_interval_s = rescan_interval_s
        self._watcher_generation = 0
        self._watcher_stop = threading.Event()

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

    def _get_embedder(self) -> Any | None:
        """Fetch an embedder, tolerating a provider that is still unavailable.

        The provider (``EmbeddingService.ensure``) is retried on every pass, so a
        server that was down at startup or unloaded by ``queryd`` comes back
        instead of leaving the indexer permanently ``embedded=0``.
        """
        try:
            embedder = self._embedder_provider()
        except Exception as exc:  # noqa: BLE001 - any failure means "text only"
            log.warning("embeddings unavailable: %s", exc)
            return None
        if embedder is None or self._on_embedder_failure is None:
            return embedder
        return _ResilientEmbedder(embedder, self._embedder_provider, self._on_embedder_failure)

    def index_once(self, roots: list[str] | None = None) -> pipeline.IndexStats:
        conn = self._ensure_conn()
        embedder = self._get_embedder()
        stats = pipeline.index_roots(
            conn, self.cfg, roots=roots, embedder=embedder, pause_check=self.pause_check
        )
        if embedder is not None and not stats.paused:
            stats.backfilled = pipeline.backfill(conn, self.cfg, embedder)
            if stats.backfilled:
                log.info("backfilled %d file(s) with vectors", stats.backfilled)
        return stats

    def index_changes(self, paths: Iterable[Path]) -> pipeline.IndexStats:
        """Index exactly the changed paths — no walk of the enclosing root."""
        conn = self._ensure_conn()
        embedder = self._get_embedder()
        stats = pipeline.index_paths(
            conn, self.cfg, paths, embedder=embedder, pause_check=self.pause_check
        )
        if embedder is not None and not stats.paused:
            stats.backfilled = pipeline.backfill(conn, self.cfg, embedder)
        return stats

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
            self._start_watcher(events)

        while not self.stop_event.is_set():
            # Give an owned llama-server a chance to unload while we wait, so it
            # does not sit in RAM (~460 MiB) forever when the indexer is the owner.
            self._run_idle_hook()
            if self._reload_config() and events is not None:
                self._restart_watcher(events)
            now = self.clock()
            if next_rescan is not None and now >= next_rescan:
                log.info("periodic rescan")
                next_rescan = self._next_rescan(self.run_once())
                continue

            timeout = QUEUE_TIMEOUT_S
            if next_rescan is not None:
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
            if self.roots_for(changed) is None:
                continue  # nothing in the batch lives under a configured root
            log.info("change in %d path(s); reindexing them", len(changed))
            self.index_changes(changed)

    def _reload_config(self) -> bool:
        """Re-read the config; returns True when the watched roots changed.

        Roots, limits, OCR settings and the rescan interval then apply without a
        restart. An unreadable or invalid file is logged and the current config is
        kept, so a typo never takes the daemon down.
        """
        if self._reload is None:
            return False
        try:
            fresh = self._reload()
        except Exception as exc:  # noqa: BLE001 - keep running with the old config
            log.warning("cannot reload the config; keeping the current one: %s", exc)
            return False
        if not fresh or fresh == self.cfg:
            return False
        old_roots = self._roots()
        self.cfg = fresh
        if not self._pause_injected:
            self.pause_check = lambda: pause_reason(self.cfg)
        if self._rescan_override is None:
            self.rescan_interval_s = float(fresh["index"]["rescan_interval_min"]) * 60
        log.info("configuration reloaded")
        return self._roots() != old_roots

    def _start_watcher(self, events: queue.Queue[set[Path] | None]) -> None:
        self._watcher_generation += 1
        generation = self._watcher_generation
        self._watcher_stop = threading.Event()
        threading.Thread(
            target=self._produce,
            args=(events, self._watcher_stop, generation),
            name=f"feagle-watch-{generation}",
            daemon=True,
        ).start()

    def _restart_watcher(self, events: queue.Queue[set[Path] | None]) -> None:
        """Re-create the watcher after the configured roots changed."""
        log.info("index roots changed; restarting the watcher")
        self._watcher_stop.set()
        self.watcher = Watcher(self._roots())
        self._start_watcher(events)

    def _run_idle_hook(self) -> None:
        if self.idle_hook is None:
            return
        with contextlib.suppress(Exception):
            self.idle_hook()

    def _next_rescan(self, stats: pipeline.IndexStats) -> float | None:
        """When the next pass is due, or ``None`` when nothing schedules one.

        A *paused* pass is not a finished one: it is retried after
        ``PAUSED_RETRY_S`` even when periodic rescans are disabled, so a busy
        machine cannot stall the queue until the next file change.
        """
        now = self.clock()
        if stats.paused:
            return now + PAUSED_RETRY_S
        if self.rescan_interval_s:
            return now + self.rescan_interval_s
        return None

    def _produce(
        self,
        events: queue.Queue[set[Path] | None],
        stop: threading.Event,
        generation: int,
    ) -> None:
        watcher = self.watcher
        assert watcher is not None  # set before the producer thread starts
        try:
            for paths in watcher.events(stop):
                if paths:
                    events.put(paths)
                if stop.is_set() or self.stop_event.is_set():
                    break
        except Exception:
            log.exception("watcher failed")
        finally:
            # Only the current generation ends the run: a watcher replaced after a
            # config reload must not stop the daemon.
            if generation == self._watcher_generation:
                events.put(None)
