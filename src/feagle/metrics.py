"""Small measurement helpers for the benchmarks (standard library only)."""

from __future__ import annotations

import statistics
from pathlib import Path


def peak_rss_mb(pid: int | None = None) -> float | None:
    """Peak resident memory (VmHWM) in MiB; ``None`` if it cannot be read."""
    status = Path("/proc/self/status") if pid is None else Path(f"/proc/{pid}/status")
    try:
        for line in status.read_text(encoding="utf-8").splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) / 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def current_rss_mb(pid: int) -> float | None:
    """Current resident memory (VmRSS) of ``pid`` in MiB."""
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def llama_server_pid(runtime_dir: Path) -> int | None:
    """PID of the shared ``llama-server`` from its pid file, if any."""
    try:
        return int((runtime_dir / "feagle" / "llama.pid").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def percentile(values: list[float], percent: float) -> float:
    """Nearest-rank percentile (fine for small samples)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((percent / 100) * (len(ordered) - 1))))
    return ordered[index]


def summarise(values: list[float]) -> dict[str, float]:
    """Return count/p50/p95/min/max/mean for ``values`` (rounded)."""
    if not values:
        return {"count": 0, "p50": 0.0, "p95": 0.0, "min": 0.0, "max": 0.0, "mean": 0.0}
    return {
        "count": len(values),
        "p50": round(percentile(values, 50), 2),
        "p95": round(percentile(values, 95), 2),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
        "mean": round(statistics.fmean(values), 2),
    }
