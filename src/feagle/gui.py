"""The launcher popup's own settings (``$XDG_CONFIG_HOME/feagle/gui.toml``).

Separate from ``config.toml`` on purpose: the popup's settings panel writes this
file while the user clicks around, and nothing it can contain may be able to
break indexing. Only the keys the panel exposes live here; anything else in the
file is reported and ignored.

Reading and writing are deliberately asymmetric:

* :func:`load_gui` is lenient — an unknown key or an out-of-range value is logged
  and the default wins, so a hand-edited file never leaves the popup unusable;
* :func:`coerce` and :func:`save_gui` are strict — ``set_gui`` answers
  ``ok:false`` instead of persisting a value the popup cannot render.

The file is written atomically with mode 0600: it is per-user state, like the
database and the query socket.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import paths
from .log import get_logger
from .setup import to_toml

if TYPE_CHECKING:
    from pathlib import Path

log = get_logger("gui")

#: The one section the file holds. A flat file (the keys at the top level) is
#: accepted on read as well, so a hand-written file is not silently ignored.
SECTION = "popup"

HEADER = """\
# feagle popup settings.
# Written by the launcher's settings panel (through the daemon, op 'set_gui');
# edit freely — a wrong value is reported and ignored, the default wins.
# Every key is documented in the project README ("Launcher popup").
"""


class GuiError(Exception):
    """A setting name or value the popup cannot use."""


#: How many pinned files ``favorites`` may hold, and how long one path may be.
MAX_FAVORITES = 100
MAX_PATH_CHARS = 4096


@dataclass(frozen=True)
class Setting:
    """One exposed key: its type, default and accepted range."""

    kind: str
    default: Any
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple[str, ...] = ()
    hint: str = ""
    hint_ru: str = ""


#: Every key the panel may read or write. `row_columns` counts the row's *text*
#: columns from the left — title, path, and the position/meta column — so 3 is
#: the full row the launcher has always drawn. The icon is not one of them: it is
#: always drawn.
SETTINGS: dict[str, Setting] = {
    "language": Setting(
        "str",
        "auto",
        choices=("auto", "en", "ru"),
        hint="interface language (auto follows the locale)",
        hint_ru="язык интерфейса (auto — по локали)",
    ),
    "show_preview": Setting(
        "bool",
        True,
        hint="preview pane for the selected file",
        hint_ru="панель превью для выбранного файла",
    ),
    "preview_width": Setting(
        "int",
        360,
        minimum=200,
        maximum=800,
        hint="preview pane width, px",
        hint_ru="ширина панели превью, px",
    ),
    "row_columns": Setting(
        "int",
        3,
        minimum=1,
        maximum=3,
        hint="text columns per row: title, path, meta",
        hint_ru="текстовых колонок в строке: название, путь, мета",
    ),
    "layout": Setting(
        "str",
        "detailed",
        choices=("compact", "detailed"),
        hint="row density",
        hint_ru="плотность строк",
    ),
    "theme": Setting(
        "str",
        "dark",
        choices=("dark", "light"),
        hint="popup colours",
        hint_ru="цвета попапа",
    ),
    "default_filter": Setting(
        "str",
        "all",
        choices=("all", "apps", "files"),
        hint="results shown on open",
        hint_ru="результаты при открытии",
    ),
    "apps_limit": Setting(
        "int",
        8,
        minimum=0,
        maximum=50,
        hint="applications ranked above file hits",
        hint_ru="приложений выше файлов при вводе",
    ),
    "debounce_ms": Setting(
        "int",
        120,
        minimum=0,
        maximum=2000,
        hint="pause before the semantic pass",
        hint_ru="пауза перед семантическим поиском",
    ),
    # `favorites` has no control in the panel (it is toggled with Ctrl+D over a
    # result), so it is deliberately absent from the panel's order.
    "favorites": Setting(
        "paths",
        [],
        hint="pinned files, shown first on an empty query (Ctrl+D)",
        hint_ru="закреплённые файлы, первыми при пустом запросе (Ctrl+D)",
    ),
}


def default_gui() -> dict[str, Any]:
    """A fresh copy of every default (lists included: callers may mutate)."""
    return {
        key: list(setting.default) if isinstance(setting.default, list) else setting.default
        for key, setting in SETTINGS.items()
    }


def gui_file() -> Path:
    """The popup settings file (``$XDG_CONFIG_HOME/feagle/gui.toml``)."""
    return paths.gui_file()


def schema() -> dict[str, dict[str, Any]]:
    """The settings as plain data, so the popup renders controls generically.

    JSON-friendly on purpose: the panel asks the daemon for this instead of
    repeating the ranges and choices in QML.
    """
    return {
        key: {
            "kind": setting.kind,
            "default": setting.default,
            "min": setting.minimum,
            "max": setting.maximum,
            "choices": list(setting.choices),
            "hint": {"en": setting.hint, "ru": setting.hint_ru or setting.hint},
        }
        for key, setting in SETTINGS.items()
    }


def coerce(key: str, value: Any) -> Any:
    """``value`` as the setting ``key`` expects, or raise :class:`GuiError`.

    Numeric strings and ``"true"``/``"false"`` are accepted: a hand-written file
    and a JSON client that sends strings both reach here, and neither should need
    a special case.
    """
    setting = SETTINGS.get(key)
    if setting is None:
        raise GuiError(f"unknown setting: {key!r}")
    if setting.kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        raise GuiError(f"{key} must be true or false")
    if setting.kind == "int":
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise GuiError(f"{key} must be an integer")
        try:
            number = int(value)
        except ValueError:
            raise GuiError(f"{key} must be an integer") from None
        if setting.minimum is not None and number < setting.minimum:
            raise GuiError(f"{key} must be >= {setting.minimum}")
        if setting.maximum is not None and number > setting.maximum:
            raise GuiError(f"{key} must be <= {setting.maximum}")
        return number
    if setting.kind == "paths":
        return _as_paths(key, value)
    if not isinstance(value, str) or value not in setting.choices:
        raise GuiError(f"{key} must be one of " + ", ".join(setting.choices))
    return value


def _as_paths(key: str, value: Any) -> list[str]:
    """A list of absolute paths (pins); empty entries are dropped."""
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise GuiError(f"{key} must be a list of paths")
    paths = [path for path in value if path]
    if len(paths) > MAX_FAVORITES:
        raise GuiError(f"{key} holds at most {MAX_FAVORITES} paths")
    for path in paths:
        if not path.startswith("/") or len(path) > MAX_PATH_CHARS:
            raise GuiError(f"{key}: paths must be absolute and shorter than {MAX_PATH_CHARS}")
    return paths


def load_gui(path: Path | None = None) -> dict[str, Any]:
    """The merged settings; a missing or unusable file yields the defaults."""
    result = default_gui()
    path = path or paths.gui_file()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return result
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log.warning("gui.toml: %s; using defaults", exc)
        return result

    if SECTION in data:
        raw = data[SECTION]
        for key in data.keys() - {SECTION}:
            log.warning("gui.toml: unknown section %r ignored", key)
    else:
        raw = data
    if not isinstance(raw, dict):
        log.warning("gui.toml: [%s] is not a table; using defaults", SECTION)
        return result

    for key, value in raw.items():
        if key not in SETTINGS:
            log.warning("gui.toml: unknown setting %r ignored", key)
            continue
        try:
            result[key] = coerce(key, value)
        except GuiError as exc:
            log.warning("gui.toml: %s; using %r", exc, result[key])
    return result


def save_gui(values: dict[str, Any], path: Path | None = None) -> Path:
    """Validate ``values`` and write the file atomically, mode 0600."""
    unknown = sorted(set(values) - set(SETTINGS))
    if unknown:
        raise GuiError("unknown setting(s): " + ", ".join(unknown))
    rendered = {key: coerce(key, value) for key, value in values.items()}

    path = path or paths.gui_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = HEADER + "\n" + to_toml({SECTION: rendered})
    # `mkstemp` creates the file 0600 for us; `os.replace` keeps the swap atomic
    # so a crash mid-write cannot leave a truncated gui.toml behind.
    handle_fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    return path


def set_gui(changes: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    """Apply ``changes`` on top of the current file; return the new settings.

    Every value is validated before anything is written, so an accepted reply
    means the file on disk matches the returned settings.
    """
    if not changes:
        raise GuiError("set_gui needs at least one setting")
    values = load_gui(path)
    for key, value in changes.items():
        values[key] = coerce(key, value)
    save_gui(values, path)
    return values
