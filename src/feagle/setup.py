"""Writing the configuration during ``feagle setup``.

Pure helpers (no prompts) so the interesting parts are testable: rendering the
merged config back to TOML, backing up an existing file, and applying a plan.
"""

from __future__ import annotations

import copy
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import paths

HEADER = """\
# feagle configuration.
# Written by 'feagle setup'. Every value is documented in the project README.
# Edit freely; 'feagle doctor' explains anything that is wrong.
"""


@dataclass
class SetupPlan:
    """What ``feagle setup`` should apply."""

    roots: list[str] = field(default_factory=list)
    model: str | None = None
    mrl_dim: int | None = None
    launcher: str | None = None
    media_images: bool = False
    media_video: bool = False
    media_audio: bool = False
    download: bool = True
    install_units: bool = True
    enable_units: bool = True
    run_index: bool = True
    backup: bool = True


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, list):
        return "[" + ", ".join(_format_value(item) for item in value) + "]"
    raise TypeError(f"cannot render {type(value).__name__} in TOML")


def to_toml(cfg: dict[str, Any]) -> str:
    """Render a config dict back to TOML (one level of nesting)."""
    blocks: list[str] = []
    for section, values in cfg.items():
        lines = [f"[{section}]"]
        for key, value in values.items():
            lines.append(f"{key} = {_format_value(value)}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def _backup_path(path: Path, *, now: str | None = None) -> Path:
    """A free ``<name>.bak-YYYYmmdd-HHMMSS`` path next to ``path``.

    A fixed ``.bak`` name meant a second ``feagle setup`` overwrote the first
    backup; the timestamp keeps every revision, and a numeric suffix covers two
    runs inside the same second.
    """
    stamp = now or time.strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.name}.bak-{stamp}")
    counter = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.bak-{stamp}-{counter}")
        counter += 1
    return candidate


def write_config(
    cfg: dict[str, Any], path: Path | None = None, *, backup: bool = True
) -> tuple[Path, Path | None]:
    """Write ``cfg`` to ``path``, backing up an existing file first."""
    path = path or paths.config_file()
    path.parent.mkdir(parents=True, exist_ok=True)

    backup_path: Path | None = None
    if path.exists() and backup:
        backup_path = _backup_path(path)
        shutil.copy2(path, backup_path)

    path.write_text(HEADER + "\n" + to_toml(cfg), encoding="utf-8")
    return path, backup_path


def default_roots(home: Path | None = None) -> list[str]:
    """Suggest common document directories that actually exist."""
    base = home or Path.home()
    candidates = [base / "Documents", base / "Downloads", base / "Desktop", base / "src"]
    return [str(path) for path in candidates if path.is_dir()]


def apply_plan(cfg: dict[str, Any], plan: SetupPlan) -> dict[str, Any]:
    """Return a copy of ``cfg`` with the plan's choices applied."""
    result = copy.deepcopy(cfg)
    if plan.roots:
        roots: list[str] = []
        for root in plan.roots:
            expanded = str(Path(root).expanduser())
            if expanded not in roots:
                roots.append(expanded)
        result["index"]["roots"] = roots
    if plan.model:
        result["embeddings"]["model"] = plan.model
    if plan.mrl_dim:
        result["embeddings"]["mrl_dim"] = plan.mrl_dim
    if plan.launcher:
        result["launcher"]["program"] = plan.launcher
    if plan.media_images:
        result["media"]["images"] = True
    if plan.media_video:
        result["media"]["video"] = True
    if plan.media_audio:
        result["media"]["audio"] = True
    return result
