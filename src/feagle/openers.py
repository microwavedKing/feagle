"""Open a file at a position using the user's configured opener templates.

Templates live under ``[openers]`` in the config. Placeholders are ``{path}``,
``{line}``, ``{page}`` and ``{time}`` (seconds, for media). When a position is not
given, the placeholder *and* a directly preceding flag (``--page {page}``) are
dropped, so a template like ``zathura --page {page} {path}`` degrades to
``zathura {path}``.

Commands are split with :func:`shlex.split` and executed without a shell, so
paths can never be interpreted as shell syntax.
"""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .log import get_logger

log = get_logger("openers")

_POSITION_PLACEHOLDERS = ("line", "page", "time")


class OpenError(Exception):
    """The opener could not be built or launched."""


def select_template(openers: dict[str, str], path: Path) -> str:
    """Pick the opener for ``path``'s extension, falling back to ``default``."""
    extension = path.suffix.lower().lstrip(".")
    if extension and extension in openers:
        return openers[extension]
    return openers["default"]


def _has_missing(token: str, missing: set[str]) -> bool:
    return any(f"{{{name}}}" in token for name in missing)


def build_command(
    template: str,
    *,
    path: Path,
    line: int | None = None,
    page: int | None = None,
    time_ms: int | None = None,
) -> list[str]:
    """Expand ``template`` into an argv list, dropping absent positions."""
    values: dict[str, str] = {"path": str(path)}
    if line is not None:
        values["line"] = str(line)
    if page is not None:
        values["page"] = str(page)
    if time_ms is not None:
        values["time"] = f"{time_ms / 1000:g}"

    missing = {p for p in _POSITION_PLACEHOLDERS if p not in values}

    tokens = shlex.split(template)
    command: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        following = tokens[index + 1] if index + 1 < len(tokens) else None

        # a flag that expects a missing placeholder as its value
        if token.startswith(("-", "+")) and following is not None and _has_missing(
            following, missing
        ):
            index += 2
            continue
        # a token that is (or contains) a missing placeholder
        if _has_missing(token, missing):
            index += 1
            continue

        command.append(token.format(**values))
        index += 1
    return command


def open_result(
    openers: dict[str, str],
    path: Path,
    *,
    line: int | None = None,
    page: int | None = None,
    time_ms: int | None = None,
    spawn: Callable[[list[str]], Any] | None = None,
) -> list[str]:
    """Launch the opener for ``path`` and return the argv that was used."""
    template = select_template(openers, path)
    command = build_command(template, path=path, line=line, page=page, time_ms=time_ms)
    if not command:
        raise OpenError(f"opener for {path.name!r} expands to an empty command")

    runner = spawn or subprocess.Popen
    try:
        runner(command)
    except FileNotFoundError as exc:
        raise OpenError(f"opener not found: {command[0]!r} ({exc})") from exc
    except OSError as exc:
        raise OpenError(f"cannot launch {command[0]!r}: {exc}") from exc
    log.info("opened %s with %s", path, command[0])
    return command
