"""feagle command-line entry point."""

from __future__ import annotations

import argparse
import sys

from .. import __version__, config
from . import cmd_index, cmd_search, cmd_status


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
    index.set_defaults(func=cmd_index.run)

    search = subparsers.add_parser("search", help="search the index")
    search.add_argument("query", help="search query")
    search.add_argument("--limit", type=int, default=None, help="maximum number of results")
    search.add_argument("--json", action="store_true", help="emit JSON")
    search.set_defaults(func=cmd_search.run)

    status = subparsers.add_parser("status", help="show index statistics")
    status.add_argument("--json", action="store_true", help="emit JSON")
    status.set_defaults(func=cmd_status.run)

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
