"""Installing the systemd user units.

The canonical unit text lives here as templates with a ``{feagle}`` placeholder
for the executable path, so a pipx/uv install can point ``ExecStart`` at the real
binary. The files under ``systemd/`` in the repository are the same templates
rendered with ``/usr/bin/feagle`` (verified by a test).
"""

from __future__ import annotations

import contextlib
import shutil
import stat
import sys
from pathlib import Path

from . import paths
from .log import get_logger

log = get_logger("units")

UNIT_NAMES = ("feagle-indexerd.service", "feagle-queryd.service")

_TEMPLATES: dict[str, str] = {
    "feagle-indexerd.service": """\
[Unit]
Description=feagle indexer (watch documents and keep the index fresh)
Documentation=https://github.com/microwavedKing/feagle
After=default.target

[Service]
Type=simple
ExecStart={feagle} indexerd
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1
RuntimeDirectory=feagle
# Stay out of the way: lowest priority, idle I/O.
Nice=19
IOSchedulingClass=idle
# Cap CPU usage if needed. 200% = two CPUs (percent of a single CPU).
#CPUQuota=200%

[Install]
WantedBy=default.target
""",
    "feagle-queryd.service": """\
[Unit]
Description=feagle query daemon (full-text and semantic search on a unix socket)
Documentation=https://github.com/microwavedKing/feagle
After=default.target

[Service]
Type=simple
ExecStart={feagle} queryd
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1
RuntimeDirectory=feagle

[Install]
WantedBy=default.target
""",
}


def template(name: str) -> str:
    try:
        return _TEMPLATES[name]
    except KeyError:
        raise KeyError(f"unknown unit {name!r}; known: {', '.join(UNIT_NAMES)}") from None


def render_unit(name: str, feagle_exec: str) -> str:
    """Render a unit with the given absolute ``feagle`` executable path."""
    return template(name).format(feagle=feagle_exec)


def user_unit_dir() -> Path:
    """``$XDG_CONFIG_HOME/systemd/user`` (where user units are read from)."""
    return paths.config_home() / "systemd" / "user"


def installed_paths() -> list[Path]:
    return [user_unit_dir() / name for name in UNIT_NAMES]


def exec_path(default: str = "feagle") -> str:
    """Absolute path of the running ``feagle`` executable.

    systemd does not inherit the shell's ``PATH``, and a venv/pipx install is
    usually not in the system-wide ``PATH``, so a bare ``feagle`` in
    ``ExecStart`` would not resolve. We therefore look at how we were invoked
    (``sys.argv[0]``), next to the running interpreter, and finally on ``PATH``.
    """
    candidates: list[Path] = []

    argv0 = sys.argv[0] if sys.argv else ""
    if argv0 and Path(argv0).name == default:
        candidates.append(Path(argv0))

    candidates.append(Path(sys.executable).parent / default)

    on_path = shutil.which(default)
    if on_path:
        candidates.append(Path(on_path))

    for candidate in candidates:
        with contextlib.suppress(OSError):
            if candidate.is_file():
                return str(candidate.resolve())
    return default


def install(feagle_exec: str | None = None) -> list[Path]:
    """Write the user units to ``$XDG_CONFIG_HOME/systemd/user``."""
    target_dir = user_unit_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    executable = feagle_exec or exec_path()

    written: list[Path] = []
    for name in UNIT_NAMES:
        path = target_dir / name
        path.write_text(render_unit(name, executable), encoding="utf-8")
        path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
        written.append(path)
        log.info("installed unit %s", path)
    return written


def are_installed() -> bool:
    return all(path.is_file() for path in installed_paths())
