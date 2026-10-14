"""Watch index roots for changes.

``watchfiles`` (inotify, with a polling fallback) runs in a background thread and
hands batches of changed paths to the caller. Debouncing happens inside
``watchfiles``; the daemon then re-scans only the configured roots that are
affected, so deletions are still handled by the pipeline's prune step.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

from ..log import get_logger

log = get_logger("indexer.watcher")

DEFAULT_DEBOUNCE_MS = 2000
DEFAULT_STEP_MS = 200

#: A source yields batches of changed file paths until it is stopped.
ChangeSource = Callable[[threading.Event], Iterator[set[Path]]]


def watched_paths(changes: Iterable[Any]) -> set[Path]:
    """Extract the paths from a ``watchfiles`` change set."""
    return {Path(path) for _change, path in changes}


def _watchfiles_source(
    roots: list[Path], *, debounce_ms: int, step_ms: int, poll: bool
) -> ChangeSource:
    def source(stop_event: threading.Event) -> Iterator[set[Path]]:
        from watchfiles import watch

        for changes in watch(
            *[str(root) for root in roots],
            debounce=debounce_ms,
            step=step_ms,
            stop_event=stop_event,
            force_polling=poll,
        ):
            yield watched_paths(changes)

    return source


class Watcher:
    """Produces batches of changed paths for the configured roots."""

    def __init__(
        self,
        roots: Iterable[Path | str],
        *,
        debounce_ms: int = DEFAULT_DEBOUNCE_MS,
        step_ms: int = DEFAULT_STEP_MS,
        poll: bool = False,
        source: ChangeSource | None = None,
    ) -> None:
        self.roots = [Path(root) for root in roots]
        self.debounce_ms = debounce_ms
        self.step_ms = step_ms
        self.poll = poll
        self._source = source or _watchfiles_source(
            self.roots, debounce_ms=debounce_ms, step_ms=step_ms, poll=poll
        )

    def events(self, stop_event: threading.Event) -> Iterator[set[Path]]:
        yield from self._source(stop_event)
