"""Desktop applications from the XDG menu specification.

``.desktop`` files are parsed directly (no dependency): name, exec line,
terminal flag, and the ``NoDisplay``/``Hidden`` keys that hide an entry. The
desktop-file id is the path relative to the ``applications`` directory with
``/`` replaced by ``-``, which is what ``gtk-launch``/``gio`` expect.

Launching prefers ``gtk-launch`` and ``gio launch`` (they handle the entry the
way the desktop does) and falls back to running the ``Exec`` line with the field
codes removed.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .log import get_logger

log = get_logger("apps")

#: Desktop-entry field codes that must be removed before running ``Exec``.
_FIELD_CODES = {"%f", "%F", "%u", "%U", "%d", "%D", "%n", "%N", "%i", "%c", "%k", "%v", "%m"}

#: Terminals tried (in order) when an entry asks for ``Terminal=true``.
_TERMINALS = ("kitty", "alacritty", "wezterm", "foot", "gnome-terminal", "konsole", "xterm")


@dataclass(frozen=True)
class App:
    id: str
    name: str
    exec_line: str
    terminal: bool
    path: Path


def data_dirs() -> list[Path]:
    """``$XDG_DATA_HOME`` followed by ``$XDG_DATA_DIRS`` (user entries first)."""
    home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    dirs = [Path(home)]
    raw = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    dirs += [Path(part) for part in raw.split(":") if part]
    return dirs


def desktop_dirs() -> list[Path]:
    return [base / "applications" for base in data_dirs()]


def _read_entry(path: Path) -> dict[str, str] | None:
    """Return the ``[Desktop Entry]`` keys of a ``.desktop`` file."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    values: dict[str, str] = {}
    in_section = False
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_section = line == "[Desktop Entry]"
            continue
        if not in_section or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values.setdefault(key.strip(), value.strip())
    return values


def parse_desktop_file(path: Path, root: Path) -> App | None:
    """Parse one entry; ``None`` when it is hidden, not an app, or malformed."""
    values = _read_entry(path)
    if values is None:
        return None
    if values.get("Type", "Application") != "Application":
        return None
    if values.get("NoDisplay", "false").lower() == "true":
        return None
    if values.get("Hidden", "false").lower() == "true":
        return None
    name = values.get("Name")
    exec_line = values.get("Exec")
    if not name or not exec_line:
        return None
    try_exec = values.get("TryExec")
    if try_exec and shutil.which(try_exec) is None:
        return None

    relative = path.relative_to(root).with_suffix("")
    app_id = str(relative).replace("/", "-") + ".desktop"
    return App(
        id=app_id,
        name=name,
        exec_line=exec_line,
        terminal=values.get("Terminal", "false").lower() == "true",
        path=path,
    )


def discover_apps(dirs: list[Path] | None = None) -> list[App]:
    """All visible applications, user entries overriding system ones by id."""
    found: dict[str, App] = {}
    for root in dirs if dirs is not None else desktop_dirs():
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.desktop")):
            app = parse_desktop_file(path, root)
            if app is not None:
                # later directories (system) must not overwrite user entries
                found.setdefault(app.id, app)
    return sorted(found.values(), key=lambda app: app.name.casefold())


def default_terminal() -> str | None:
    for candidate in (os.environ.get("TERMINAL"), *_TERMINALS):
        if candidate and shutil.which(candidate):
            return candidate
    return None


def exec_argv(app: App) -> list[str]:
    """The ``Exec`` line as an argv list with field codes removed."""
    tokens = [token for token in shlex.split(app.exec_line) if token not in _FIELD_CODES]
    return tokens


def launch_command(app: App) -> list[str]:
    """Preferred way to start ``app`` (``gtk-launch`` → ``gio`` → ``Exec``)."""
    if shutil.which("gtk-launch"):
        return ["gtk-launch", app.id]
    if shutil.which("gio"):
        return ["gio", "launch", str(app.path)]
    argv = exec_argv(app)
    if app.terminal:
        terminal = default_terminal()
        if terminal is None:
            return argv
        return [terminal, "-e", *argv] if terminal in {"xterm", "alacritty"} else [terminal, *argv]
    return argv


def launch(app: App, *, spawn: Any = None) -> list[str]:
    """Start ``app``; returns the argv used. Raises ``OSError`` on failure."""
    command = launch_command(app)
    if not command:
        raise OSError(f"cannot build a command for {app.id}")
    (spawn or subprocess.Popen)(command)
    log.info("launched application %s", app.id)
    return command


def _is_subsequence(needle: str, haystack: str) -> bool:
    """Whether ``needle``'s characters appear in order inside ``haystack``."""
    iterator = iter(haystack)
    return all(char in iterator for char in needle)


def score_app(name: str, query: str) -> float:
    """A simple launcher score: exact > prefix > substring > fuzzy subsequence."""
    candidate = name.casefold()
    needle = query.casefold().strip()
    if not needle:
        return 1.0
    if candidate == needle:
        return 100.0
    if candidate.startswith(needle):
        return 50.0
    if needle in candidate:
        return 25.0
    if _is_subsequence(needle, candidate):
        return 10.0
    return 0.0


def match_apps(apps: list[App], query: str, *, limit: int = 10) -> list[tuple[float, App]]:
    """``(score, app)`` pairs that match ``query``, best first."""
    scored = [(score_app(app.name, query), app) for app in apps]
    matched = [(score, app) for score, app in scored if score > 0.0]
    matched.sort(key=lambda item: (-item[0], item[1].name.casefold()))
    return matched[:limit]
