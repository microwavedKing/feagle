"""Recent launcher queries (``$XDG_STATE_HOME/feagle/history.json``).

A short, de-duplicated list of the queries the user actually ran, newest first;
the popup walks it with ``↑``/``↓`` while the field is empty. Same forgiving
rules as the frecency store: a missing, unreadable or corrupt file simply means
"no history", writes are atomic and owner-only, and a broken history never stops
a search.
"""

from __future__ import annotations

import contextlib
import json
import os
from typing import TYPE_CHECKING

from . import paths
from .log import get_logger

if TYPE_CHECKING:
    from pathlib import Path

log = get_logger("history")

#: Keep at most this many queries (the newest ones).
MAX_ENTRIES = 100
#: Longer queries are not remembered (they are almost always pasted text).
MAX_CHARS = 500


def store_path() -> Path:
    return paths.state_dir() / "history.json"


def _read(path: Path) -> list[str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        log.warning("ignoring corrupt history file %s", path)
        return []
    if not isinstance(data, list):
        return []
    return [
        item.strip()
        for item in data
        if isinstance(item, str) and item.strip() and len(item.strip()) <= MAX_CHARS
    ][:MAX_ENTRIES]


def _mtime(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


class QueryHistory:
    """Reads and updates the query history, caching it in memory."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or store_path()
        self._entries: list[str] | None = None
        self._mtime_ns: int | None = None

    def _refresh(self) -> list[str]:
        """Load once, then reload only when the file changed on disk."""
        current = _mtime(self.path)
        if self._entries is not None and current == self._mtime_ns:
            return self._entries
        self._entries = _read(self.path)
        self._mtime_ns = current
        return self._entries

    def entries(self, limit: int = 50) -> list[str]:
        """The newest queries first."""
        return list(self._refresh()[:limit])

    def add(self, text: str) -> list[str]:
        """Remember ``text`` as the newest query and return the new list."""
        text = text.strip()
        if not text or len(text) > MAX_CHARS:
            return self.entries(MAX_ENTRIES)
        entries = [text, *(item for item in self._refresh() if item != text)]
        self._entries = entries[:MAX_ENTRIES]
        self._save(self._entries)
        return list(self._entries)

    def _save(self, entries: list[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self.path.parent, 0o700)
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(entries, handle, ensure_ascii=False, indent=0)
            with contextlib.suppress(OSError):
                os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except OSError as exc:  # best effort: history must never break a search
            log.warning("cannot write history file %s: %s", self.path, exc)
            with contextlib.suppress(OSError):
                tmp.unlink()
            return
        self._mtime_ns = _mtime(self.path)
