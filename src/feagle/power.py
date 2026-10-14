"""Battery and system-load checks used to pause indexing.

Battery state is read from ``/sys/class/power_supply``: any supply whose ``type``
is ``Battery`` and whose ``status`` is ``Discharging`` means we are on battery.
Machines without a battery never pause. System load is read from
``/proc/loadavg`` (the 1-minute value).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .log import get_logger

log = get_logger("power")

POWER_SUPPLY_DIR = Path("/sys/class/power_supply")
LOADAVG_PATH = Path("/proc/loadavg")


def on_battery(power_supply_dir: Path = POWER_SUPPLY_DIR) -> bool:
    """Whether the machine is currently running on battery power."""
    if not power_supply_dir.is_dir():
        return False
    for entry in sorted(power_supply_dir.iterdir()):
        try:
            supply_type = (entry / "type").read_text(encoding="utf-8").strip()
            status = (entry / "status").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if supply_type == "Battery" and status == "Discharging":
            return True
    return False


def load_average(path: Path = LOADAVG_PATH) -> float:
    """Return the 1-minute load average, or 0.0 if it cannot be read."""
    try:
        return float(path.read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        return 0.0


def pause_reason(
    cfg: dict[str, Any],
    *,
    power_supply_dir: Path = POWER_SUPPLY_DIR,
    loadavg_path: Path = LOADAVG_PATH,
) -> str | None:
    """Return a human-readable reason to pause indexing, or ``None``."""
    index_cfg = cfg["index"]
    if index_cfg["pause_on_battery"] and on_battery(power_supply_dir):
        return "running on battery"
    threshold = index_cfg["pause_on_loadavg"]
    if threshold and load_average(loadavg_path) > threshold:
        return f"load average {load_average(loadavg_path):.2f} above {threshold}"
    return None
