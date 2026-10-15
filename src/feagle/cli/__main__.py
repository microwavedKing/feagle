"""feagle command-line entry point."""

from __future__ import annotations

import argparse
import os
import sys

from .. import __version__, config
from ..embeddings import presets
from ..log import setup_logging
from . import (
    cmd_bench,
    cmd_doctor,
    cmd_index,
    cmd_indexerd,
    cmd_open,
    cmd_pick,
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
    search.add_argument(
        "--pick", action="store_true",
        help="choose a result in a launcher (dmenu) and open it",
    )
    search.add_argument(
        "--with-apps", action="store_true",
        help="also list desktop applications above the results (needs --pick)",
    )
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
        "--time", type=float, default=None, help="start time in seconds (media)"
    )
    open_cmd.add_argument(
        "--dry-run", action="store_true", help="print the command instead of running it"
    )
    open_cmd.set_defaults(func=cmd_open.run)

    pick = subparsers.add_parser(
        "pick", help="open a launcher with applications and indexed files"
    )
    pick.add_argument(
        "--query", default=None, help="rank the files semantically for this query"
    )
    pick.add_argument("--limit", type=int, default=None, help="results when --query is given")
    pick.add_argument(
        "--files", type=int, default=500, help="how many indexed files to list without a query"
    )
    pick.add_argument("--no-apps", action="store_true", help="only list files")
    pick.add_argument("--no-files", action="store_true", help="only list applications")
    pick.set_defaults(func=cmd_pick.run)

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

    bench_cmd = subparsers.add_parser(
        "bench", help="run synthetic indexing and query benchmarks"
    )
    bench_cmd.add_argument("--files", type=int, default=60, help="documents to generate")
    bench_cmd.add_argument("--seed", type=int, default=1, help="random seed")
    bench_cmd.add_argument("--corpus", default=None, metavar="DIR", help="corpus directory")
    bench_cmd.add_argument("--queries", type=int, default=0, help="max queries to time (0 = all)")
    bench_cmd.add_argument("--repeats", type=int, default=3, help="timing repetitions")
    bench_cmd.add_argument("--limit", type=int, default=10, help="results per query")
    bench_cmd.add_argument("--no-embed", action="store_true", help="full-text only")
    bench_cmd.add_argument("--keep-db", action="store_true", help="keep the throwaway database")
    bench_cmd.add_argument("--json", action="store_true", help="print the raw result as JSON")
    bench_cmd.add_argument("--out", default=None, metavar="FILE", help="write the result as JSON")
    bench_cmd.set_defaults(func=cmd_bench.run)

    return parser


def _log_level(command: str) -> str | None:
    """Daemons log at INFO (journald); interactive commands stay quiet.

    ``FEAGLE_LOG_LEVEL`` always wins.
    """
    if os.environ.get("FEAGLE_LOG_LEVEL"):
        return None
    return "INFO" if command in ("indexerd", "queryd") else "WARNING"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(_log_level(args.command))
    try:
        cfg = config.load_config()
    except config.ConfigError as exc:
        print(f"feagle: configuration error: {exc}", file=sys.stderr)
        return 2
    return args.func(args, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
