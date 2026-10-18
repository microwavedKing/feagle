"""``feagle open``: open a result at a line or page."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from ..indexer.asr import AUDIO_SUFFIXES
from ..indexer.media import VIDEO_SUFFIXES
from ..openers import OpenError, build_command, open_result, select_template


def position_type(text: str) -> int:
    """Argparse type for a ``+N`` position (line or page)."""
    value = text[1:] if text.startswith("+") else text
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number, got {text!r}") from None
    if number <= 0:
        raise argparse.ArgumentTypeError("position must be positive")
    return number


def run(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    path = Path(args.path).expanduser()
    if not path.exists():
        print(f"feagle: no such file: {path}", file=sys.stderr)
        return 1

    line = args.line
    page = args.page
    time_ms = None
    if getattr(args, "time", None) is not None:
        time_ms = round(args.time * 1000)
    if args.position is not None:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            page = page if page is not None else args.position
        elif suffix in VIDEO_SUFFIXES or suffix in AUDIO_SUFFIXES:
            if time_ms is None:
                time_ms = args.position * 1000
        else:
            line = line if line is not None else args.position

    if args.dry_run:
        command = build_command(
            select_template(cfg["openers"], path),
            path=path,
            line=line,
            page=page,
            time_ms=time_ms,
        )
        print(" ".join(command))
        return 0

    try:
        open_result(cfg["openers"], path, line=line, page=page, time_ms=time_ms)
    except OpenError as exc:
        print(f"feagle: {exc}", file=sys.stderr)
        return 1
    return 0
