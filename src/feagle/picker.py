"""Shared launcher interaction: applications first, then files.

A launcher can only filter a list we hand it (wofi/rofi do the filtering
client-side), so a "combined" picker is one list of ``(key, display)`` items:
applications first — which gives them priority in the results — then files.
Selecting an application launches it; selecting a file opens it at its position.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import apps, launchers
from .log import get_logger
from .openers import OpenError, open_result

log = get_logger("picker")

APP_SUFFIX = "  ·  application"


def app_items() -> tuple[list[tuple[str, str]], dict[str, apps.App]]:
    """``(items, by_id)`` for every visible desktop application."""
    items: list[tuple[str, str]] = []
    by_id: dict[str, apps.App] = {}
    for app in apps.discover_apps():
        by_id[app.id] = app
        items.append((app.id, f"{app.name}{APP_SUFFIX}"))
    return items, by_id


def run_pick(
    cfg: dict[str, Any],
    items: list[tuple[str, str]],
    app_map: dict[str, apps.App],
    *,
    hits_by_path: dict[str, dict[str, Any]] | None = None,
) -> int:
    """Show ``items`` in the launcher and act on the selection."""
    if not items:
        return 0
    choice = launchers.pick(items, program=cfg["launcher"]["program"])
    if choice is None:
        return 0  # cancelled

    app = app_map.get(choice)
    if app is not None:
        try:
            apps.launch(app)
        except OSError as exc:
            log.error("cannot launch %s: %s", app.id, exc)
            return 1
        return 0

    if not Path(choice).exists():
        return 0
    hit = (hits_by_path or {}).get(choice)
    try:
        open_result(
            cfg["openers"],
            Path(choice),
            line=None if hit is None else hit.get("line"),
            page=None if hit is None else hit.get("page"),
        )
    except OpenError as exc:
        log.error("%s", exc)
        return 1
    return 0
