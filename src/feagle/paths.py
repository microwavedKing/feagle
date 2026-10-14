"""XDG base-directory resolution and feagle's own paths.

All paths are resolved from environment variables at call time so that tests
(and callers) can override them without global state. No user-specific paths
are hardcoded: everything follows the XDG Base Directory Specification.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

APP_NAME = "feagle"


def _from_env(env: str, fallback: Path) -> Path:
    """Return an absolute path from ``env`` if set, else ``fallback``.

    Per the XDG spec, a relative value is treated as unset.
    """
    raw = os.environ.get(env)
    if raw:
        candidate = Path(raw)
        if candidate.is_absolute():
            return candidate
    return fallback


def config_home() -> Path:
    return _from_env("XDG_CONFIG_HOME", Path.home() / ".config")


def data_home() -> Path:
    return _from_env("XDG_DATA_HOME", Path.home() / ".local" / "share")


def state_home() -> Path:
    return _from_env("XDG_STATE_HOME", Path.home() / ".local" / "state")


def runtime_dir() -> Path:
    """Return ``$XDG_RUNTIME_DIR`` or the conventional per-UID fallback."""
    return _from_env("XDG_RUNTIME_DIR", Path("/run/user") / str(os.getuid()))


def config_dir() -> Path:
    return config_home() / APP_NAME


def config_file() -> Path:
    return config_dir() / "config.toml"


def data_dir() -> Path:
    return data_home() / APP_NAME


def state_dir() -> Path:
    return state_home() / APP_NAME


def db_path() -> Path:
    return data_dir() / "feagle.sqlite"


def models_dir() -> Path:
    return data_dir() / "models"


def runtime_subdir() -> Path:
    return runtime_dir() / APP_NAME


def socket_path() -> Path:
    return runtime_subdir() / "queryd.sock"


def _mkdir_private(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o700)
    return path


def ensure_data_dirs() -> None:
    """Create the persistent data directories with owner-only permissions."""
    _mkdir_private(data_dir())
    _mkdir_private(models_dir())


def ensure_state_dir() -> None:
    _mkdir_private(state_dir())


def ensure_runtime_dir() -> None:
    """Create the runtime directory (socket lives here)."""
    _mkdir_private(runtime_subdir())
