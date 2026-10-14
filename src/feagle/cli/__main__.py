"""feagle command-line entry point."""

from __future__ import annotations

import argparse
import sys

from .. import __version__, config
from ..embeddings import presets
from . import (
    cmd_doctor,
    cmd_index,
    cmd_indexerd,
    cmd_open,
    cmd_queryd,
    cmd_search,
    cmd_setup,
    cmd_status,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="feagle", description="Local semantic file search for Arch Linux + Wayland."
    )
    parser.add_argument("--version", action="version", version=f"feagle {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    index = subparsers.add_parser("index", help="scan and index configured roots")
    index.add_argument(
        "--root", action="append", default=None, metavar="PATH",
        help="index this directory instead of the configured roots (repeatable)",
    )
    index.add_argument("--full", action="store_true", help="re-extract every file")
    index.add_argument("--rebuild", action="store_true", help="wipe the index and rebuild it")
    index.add_argument(
        "--no-embed", action="store_true", help="skip embeddings (full-text indexing only)"
    )
    index.add_argument(
        "--backfill", action="store_true", help="embed chunks of files that still lack vectors"
    )
    index.set_defaults(func=cmd_index.run)

    search = subparsers.add_parser("search", help="search the index")
    search.add_argument("query", help="search query")
    search.add_argument("--limit", type=int, default=None, help="maximum number of results")
    search.add_argument(
        "--mode", choices=("auto", "hybrid", "fts"), default=None,
        help="search mode (default: from config)",
    )
    search.add_argument("--json", action="store_true", help="emit JSON")
    search.set_defaults(func=cmd_search.run)

    status = subparsers.add_parser("status", help="show index statistics")
    status.add_argument("--json", action="store_true", help="emit JSON")
    status.set_defaults(func=cmd_status.run)

    doctor = subparsers.add_parser("doctor", help="check the environment and dependencies")
    doctor.add_argument("--json", action="store_true", help="emit JSON")
    doctor.add_argument(
        "--fast", action="store_true", help="skip hashing the model file"
    )
    doctor.set_defaults(func=cmd_doctor.run)

    setup_cmd = subparsers.add_parser("setup", help="first-run interactive setup")
    setup_cmd.add_argument(
        "--root", action="append", default=None, metavar="PATH",
        help="directories to index (repeatable)",
    )
    setup_cmd.add_argument("--model", default=None, help="model preset or GGUF path")
    setup_cmd.add_argument("--mrl-dim", type=int, default=None, choices=presets.MRL_DIMS)
    setup_cmd.add_argument("--launcher", default=None, choices=cmd_setup.LAUNCHER_CHOICES)
    setup_cmd.add_argument(
        "--yes", action="store_true", help="accept defaults (non-interactive)"
    )
    setup_cmd.add_argument("--no-download", action="store_true", help="skip model download")
    setup_cmd.add_argument("--no-units", action="store_true", help="skip systemd units")
    setup_cmd.add_argument(
        "--no-enable", action="store_true",
        help="install the units but do not enable/start them",
    )
    setup_cmd.add_argument("--no-index", action="store_true", help="skip the first index")
    setup_cmd.add_argument("--no-backup", action="store_true", help="do not back up the config")
    setup_cmd.set_defaults(func=cmd_setup.run)

    open_cmd = subparsers.add_parser("open", help="open a file at a line or page")
    open_cmd.add_argument("path", help="file to open")
    open_cmd.add_argument(
        "position", nargs="?", type=cmd_open.position_type, default=None, metavar="+N",
        help="line (text) or page (PDF)",
    )
    open_cmd.add_argument("--line", type=int, default=None, help="line number")
    open_cmd.add_argument("--page", type=int, default=None, help="page number (PDF)")
    open_cmd.add_argument(
        "--dry-run", action="store_true", help="print the command instead of running it"
    )
    open_cmd.set_defaults(func=cmd_open.run)

    queryd = subparsers.add_parser("queryd", help="run the query daemon (foreground)")
    queryd.set_defaults(func=cmd_queryd.run)

    indexerd = subparsers.add_parser(
        "indexerd", help="run the indexing daemon (foreground)"
    )
    indexerd.add_argument(
        "--root", action="append", default=None, metavar="PATH",
        help="watch this directory instead of the configured roots (repeatable)",
    )
    indexerd.add_argument("--once", action="store_true", help="do a single pass and exit")
    indexerd.add_argument(
        "--poll", action="store_true", help="force polling instead of inotify"
    )
    indexerd.set_defaults(func=cmd_indexerd.run)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = config.load_config()
    except config.ConfigError as exc:
        print(f"feagle: configuration error: {exc}", file=sys.stderr)
        return 2
    return args.func(args, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
