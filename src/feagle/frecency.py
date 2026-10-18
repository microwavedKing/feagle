"""Launch history for launcher ranking ("frecency").

A small JSON store in ``$XDG_STATE_HOME/feagle/frecency.json`` remembers how
often and how recently each item (an application's desktop id) was launched.
Ranking uses a *frecency* score — the launch count decayed by age with a fixed
half-life — so a frequently used application stays near the top while one used
once months ago fades away.

The store is deliberately forgiving: a missing, unreadable or corrupt file is
treated as empty, and writes are atomic (temporary file + rename) with owner-only
permissions. Both the query daemon and the CLI may write it, so a cheap mtime
check reloads the file when another process changed it; the last writer wins.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import paths
from .log import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

log = get_logger("frecency")

#: The score halves every this many days.
HALF_LIFE_DAYS = 30.0
#: Entries whose score decays below this are dropped when the store is saved.
MIN_SCORE = 0.05
#: Keep at most this many entries (the highest-scoring ones).
MAX_ENTRIES = 500
#: A score at or above this counts as "fully warm" (caps the ranking bonus).
SATURATION = 8.0


@dataclass(frozen=True)
class Entry:
    """How often (``count``) and when (``last``, epoch seconds) an item was used."""

    count: int
    last: float


def store_path() -> Path:
    return paths.state_dir() / "frecency.json"


def score(entry: Entry, now: float) -> float:
    """The frecency of one entry: count decayed by age."""
    age_days = max(0.0, (now - entry.last) / 86400.0)
    return entry.count * 0.5 ** (age_days / HALF_LIFE_DAYS)


def _parse_entry(value: object) -> Entry | None:
    if not isinstance(value, dict):
        return None
    count = value.get("count")
    last = value.get("last")
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        return None
    if not isinstance(last, (int, float)) or isinstance(last, bool):
        return None
    return Entry(count=count, last=float(last))


class FrecencyStore:
    """Reads and updates the launch history, caching it in memory."""

    def __init__(
        self, path: Path | None = None, *, clock: Callable[[], float] = time.time
    ) -> None:
        self.path = path or store_path()
        self._clock = clock
        self._entries: dict[str, Entry] = {}
        self._mtime_ns: int | None = None
        self._loaded = False

    def _read(self) -> tuple[dict[str, Entry], int | None]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            return {}, None
        try:
            data = json.loads(raw)
        except ValueError:
            log.warning("ignoring corrupt frecency file %s", self.path)
            return {}, None
        if not isinstance(data, dict):
            return {}, None
        entries: dict[str, Entry] = {}
        for key, value in data.items():
            if not isinstance(key, str):
                continue
            entry = _parse_entry(value)
            if entry is not None:
                entries[key] = entry
        return entries, _mtime(self.path)

    def _refresh(self) -> None:
        """Load once, then reload only when the file changed on disk."""
        current = _mtime(self.path)
        if self._loaded and current == self._mtime_ns:
            return
        self._entries, self._mtime_ns = self._read()
        self._loaded = True

    def scores(self) -> dict[str, float]:
        """The current frecency of every remembered item."""
        self._refresh()
        now = self._clock()
        return {key: score(entry, now) for key, entry in self._entries.items()}

    def record(self, key: str) -> None:
        """Count one use of ``key`` now and persist the history."""
        if not key:
            return
        self._refresh()
        now = self._clock()
        previous = self._entries.get(key)
        self._entries[key] = Entry(
            count=1 if previous is None else previous.count + 1, last=now
        )
        self._prune(now)
        self._save()

    def _prune(self, now: float) -> None:
        kept = {
            key: entry
            for key, entry in self._entries.items()
            if score(entry, now) >= MIN_SCORE
        }
        if len(kept) > MAX_ENTRIES:
            best = sorted(
                kept.items(), key=lambda item: score(item[1], now), reverse=True
            )[:MAX_ENTRIES]
            kept = dict(best)
        self._entries = kept

    def _save(self) -> None:
        payload = {
            key: {"count": entry.count, "last": entry.last}
            for key, entry in self._entries.items()
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self.path.parent, 0o700)
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            with contextlib.suppress(OSError):
                os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except OSError as exc:  # best effort: history must never break a launch
            log.warning("cannot write frecency file %s: %s", self.path, exc)
            with contextlib.suppress(OSError):
                tmp.unlink()
            return
        self._mtime_ns = _mtime(self.path)


def _mtime(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None
