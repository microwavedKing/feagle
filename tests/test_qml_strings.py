"""The popup's Russian strings must cover every string it asks for.

`FeagleLauncher.t()` falls back to the English text when a key is missing, which
is the right runtime behaviour and the wrong silent failure: a typo in a key or a
string added without a translation would just show English. This test reads the
QML and compares the `t("...")` keys with the `ru` table in both directions.
"""

from __future__ import annotations

import re
from pathlib import Path

LAUNCHER = Path(__file__).resolve().parent.parent / "quickshell" / "FeagleLauncher.qml"

_STRING = r'"((?:[^"\\]|\\.)*)"'


def _t_keys(text: str) -> set[str]:
    """Every string literal passed to `root.t(...)`, including ternaries."""
    keys: set[str] = set()
    for match in re.finditer(r"root\.t\(", text):
        depth = 1
        index = match.end()
        while depth and index < len(text):
            if text[index] == "(":
                depth += 1
            elif text[index] == ")":
                depth -= 1
            index += 1
        keys.update(re.findall(_STRING, text[match.end():index - 1]))
    return keys


def _ru_table(text: str) -> set[str]:
    start = text.index("readonly property var ru")
    block = text[start:]
    block = block[:block.index("\n    })")]
    return {key for key, _value in re.findall(_STRING + r"\s*:\s*" + _STRING, block)}


def test_every_requested_string_is_translated():
    text = LAUNCHER.read_text(encoding="utf-8")
    requested = _t_keys(text)
    translated = _ru_table(text)

    assert requested, "no root.t(...) calls found — did the helper get renamed?"
    assert requested - translated == set(), "not translated to Russian"
    assert translated - requested == set(), "translated but never used"


def test_the_language_setting_is_looked_up():
    """The switch must read `language`, not a hard-coded locale."""
    text = LAUNCHER.read_text(encoding="utf-8")
    assert 'root.setting("language", "auto")' in text
    assert 'root.language !== "ru"' in text
