"""Launcher adapters (dmenu protocol).

A launcher reads candidate lines from stdin and writes the chosen line to
stdout. We feed ``path<TAB>snippet`` and take the path back: fuzzel is asked to
display the snippet and return the path (``--with-nth`` / ``--accept-nth``),
while the others return the whole line and we split on the first TAB.

Everything is stdin/stdout — no compositor or launcher is required to be known
at build time. When nothing is installed, ``stdout`` prints a numbered list and
reads a number instead.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

STDOUT_LAUNCHER = "stdout"

#: Detection order (first installed wins). fuzzel first: it supports the columns.
DETECTION_ORDER = ("fuzzel", "wofi", "rofi", "tofi", "walker")


@dataclass(frozen=True)
class LauncherSpec:
    name: str
    command: list[str]
    #: Whether the launcher returns only the first column (path).
    returns_path: bool = False


SPECS: dict[str, LauncherSpec] = {
    "fuzzel": LauncherSpec(
        "fuzzel",
        [
            "fuzzel", "--dmenu",
            "--with-nth=2", "--accept-nth=1",
            "--no-run-if-empty",
            "--log-level", "error",
        ],
        returns_path=True,
    ),
    "wofi": LauncherSpec("wofi", ["wofi", "--dmenu"]),
    "rofi": LauncherSpec("rofi", ["rofi", "-dmenu", "-i"]),
    "tofi": LauncherSpec("tofi", ["tofi", "--require-match=false"]),
    "walker": LauncherSpec("walker", ["walker", "--dmenu"]),
    STDOUT_LAUNCHER: LauncherSpec(STDOUT_LAUNCHER, []),
}


def available(name: str) -> bool:
    return name == STDOUT_LAUNCHER or shutil.which(name) is not None


def detect(program: str = "auto") -> str:
    """Resolve ``[launcher].program`` to a concrete launcher name."""
    if program and program != "auto":
        return program if available(program) else STDOUT_LAUNCHER
    for name in DETECTION_ORDER:
        if available(name):
            return name
    return STDOUT_LAUNCHER


def command_for(name: str) -> list[str]:
    spec = SPECS.get(name)
    if spec is None:
        raise KeyError(f"unknown launcher {name!r}")
    return list(spec.command)


def returns_path(name: str) -> bool:
    spec = SPECS.get(name)
    return bool(spec and spec.returns_path)


def _default_runner(command: list[str], text: str) -> str:
    proc = subprocess.run(command, input=text, capture_output=True, text=True, check=False)
    return proc.stdout


def _pick_stdout(
    items: Sequence[tuple[str, str]], out: TextIO, read_line: Callable[[], str]
) -> str | None:
    for index, (path, display) in enumerate(items, start=1):
        print(f"{index:>3}. {path}", file=out)
        print(f"     {display}", file=out)
    print("Select a number and press Enter (empty to cancel): ", end="", file=out, flush=True)
    try:
        answer = read_line()
    except (EOFError, KeyboardInterrupt):
        return None
    answer = answer.strip()
    if not answer:
        return None
    try:
        number = int(answer)
    except ValueError:
        return None
    if 1 <= number <= len(items):
        return items[number - 1][0]
    return None


def pick(
    items: Sequence[tuple[str, str]],
    *,
    program: str = "auto",
    runner: Callable[[list[str], str], str] | None = None,
    out: TextIO | None = None,
    read_line: Callable[[], str] | None = None,
) -> str | None:
    """Let the user choose from ``(path, display)`` items. ``None`` = cancelled."""
    if not items:
        return None

    out = out if out is not None else sys.stdout
    read_line = read_line or sys.stdin.readline
    name = detect(program)
    if name == STDOUT_LAUNCHER:
        return _pick_stdout(items, out, read_line)

    text = "".join(f"{path}\t{display}\n" for path, display in items)
    run = runner or _default_runner
    try:
        output = run(command_for(name), text)
    except FileNotFoundError:
        return _pick_stdout(items, out, read_line)

    output = (output or "").strip()
    if not output:
        return None  # the user pressed Escape
    return output.split("\t", 1)[0]
