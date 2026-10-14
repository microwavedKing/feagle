"""Thin re-export of :mod:`feagle.metrics` so the scripts in ``bench/`` can be
run directly (with the package importable) without duplicating the helpers."""

from __future__ import annotations

from feagle.metrics import (  # noqa: F401
    current_rss_mb,
    llama_server_pid,
    peak_rss_mb,
    percentile,
    summarise,
)

__all__ = ["current_rss_mb", "llama_server_pid", "peak_rss_mb", "percentile", "summarise"]
