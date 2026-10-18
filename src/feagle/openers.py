"""Open a file at a position using the user's configured opener templates.

Templates live under ``[openers]`` in the config. Placeholders are ``{path}``,
``{line}``, ``{page}`` and ``{time}`` (seconds, for media); an optional format
spec is honoured (``{page:>3}``). When a position is not given, the placeholder
*and* a directly preceding flag (``--page {page}``) are dropped, so a template like
``zathura --page {page} {path}`` degrades to ``zathura {path}``. Braces other than
the four placeholders are left as they are, so a template may contain a literal
``{`` (a shell or ``awk`` snippet) without breaking.

Commands are split with :func:`shlex.split` and executed without a shell, so
paths can never be interpreted as shell syntax.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from typing import TYPE_CHECKING, Any

from .log import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

log = get_logger("openers")

_POSITION_PLACEHOLDERS = ("line", "page", "time")

#: A documented placeholder, with an optional format spec (``{page:>3}``).
_PLACEHOLDER_RE = re.compile(r"\{(?P<name>path|line|page|time)(?P<spec>:[^{}]*)?\}")


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


def _expand(token: str, values: dict[str, str]) -> str:
    """Replace the documented placeholders and leave every other brace alone.

    ``str.format`` over the whole token would raise on a template that contains a
    literal brace - an ``awk`` one-liner in ``[openers]``, a shell expansion, or a
    typo like ``{pth}`` - and a config mistake would end as a traceback instead of
    a message. Here only ``{path}``, ``{line}``, ``{page}`` and ``{time}`` are
    substituted (with the usual optional format spec); anything else passes
    through literally.
    """

    def replace(match: re.Match[str]) -> str:
        value = values[match.group("name")]
        spec = match.group("spec")
        return format(value, spec[1:]) if spec else value

    return _PLACEHOLDER_RE.sub(replace, token)


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

        command.append(_expand(token, values))
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
