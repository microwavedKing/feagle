"""Minimal ``.gitignore`` matcher.

This is intentionally a small subset of gitignore semantics, enough for the
common cases. Supported:

- comments (``#``), blank lines, trailing whitespace stripping;
- negation (``!pattern``), last matching rule wins;
- ``*``, ``?`` and ``[...]`` character classes (``*`` never crosses ``/``);
- ``**`` (e.g. ``a/**/b``, ``**/foo``, ``logs/**``);
- trailing ``/`` (directory-only rules);
- a leading ``/`` or an inner ``/`` anchors the pattern to the base directory;
  otherwise the pattern matches the basename at any depth.

Not supported (documented limitation): escaped trailing spaces, ``\\#``/``\\!``
escapes, and re-including a path whose parent directory was ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


def _translate_glob(pattern: str) -> str:
    """Translate a gitignore glob body into a regular expression fragment."""
    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        char = pattern[i]
        if char == "*":
            if pattern[i : i + 2] == "**":
                if pattern[i + 2 : i + 3] == "/":
                    out.append("(?:.*/)?")
                    i += 3
                else:
                    out.append(".*")
                    i += 2
            else:
                out.append("[^/]*")
                i += 1
        elif char == "?":
            out.append("[^/]")
            i += 1
        elif char == "[":
            j = i + 1
            if j < n and pattern[j] in "!^":
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 1
            if j >= n:
                out.append(re.escape("["))
                i += 1
            else:
                body = pattern[i + 1 : j]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body + "]")
                i = j + 1
        else:
            out.append(re.escape(char))
            i += 1
    return "".join(out)


@dataclass(frozen=True)
class _Rule:
    regex: re.Pattern[str]
    negated: bool
    dir_only: bool
    basename_only: bool


class GitIgnore:
    """A single ``.gitignore`` file rooted at ``base``."""

    def __init__(self, base: Path, patterns: list[str]) -> None:
        self.base = base
        self.rules: list[_Rule] = []
        for raw in patterns:
            rule = self._parse(raw)
            if rule is not None:
                self.rules.append(rule)

    @classmethod
    def from_file(cls, path: Path) -> GitIgnore:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        return cls(path.parent, text.splitlines())

    @staticmethod
    def _parse(raw: str) -> _Rule | None:
        line = raw.rstrip()
        if not line or line.startswith("#"):
            return None

        negated = line.startswith("!")
        if negated:
            line = line[1:]
        if not line:
            return None

        dir_only = line.endswith("/")
        if dir_only:
            line = line[:-1]
        if not line:
            return None

        anchored = line.startswith("/") or "/" in line
        line = line.lstrip("/")
        basename_only = not anchored
        regex = re.compile(_translate_glob(line))
        return _Rule(regex, negated, dir_only, basename_only)

    def match(self, path: Path, is_dir: bool = False) -> bool | None:
        """Return ``True`` (ignored), ``False`` (negated) or ``None`` (no match)."""
        try:
            rel = path.relative_to(self.base).as_posix()
        except ValueError:
            return None
        if rel in ("", "."):
            return None

        basename = Path(rel).name
        for rule in reversed(self.rules):
            if rule.dir_only and not is_dir:
                continue
            candidate = basename if rule.basename_only else rel
            if rule.regex.fullmatch(candidate):
                return not rule.negated
        return None


def matches_chain(chain: list[GitIgnore], path: Path, is_dir: bool = False) -> bool:
    """Evaluate a stack of ``.gitignore`` files, innermost (last) winning."""
    for matcher in reversed(chain):
        result = matcher.match(path, is_dir=is_dir)
        if result is not None:
            return result
    return False
