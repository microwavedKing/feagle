"""Generate a synthetic benchmark corpus (thin wrapper around the package).

Personal files are never read; the corpus is written to ``--out`` only.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from feagle.bench import generate_corpus


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a deterministic synthetic corpus for benchmarks."
    )
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    parser.add_argument("--files", type=int, default=60, help="number of documents")
    parser.add_argument("--seed", type=int, default=1, help="random seed")
    args = parser.parse_args(argv)

    manifest = generate_corpus(args.out, files=args.files, seed=args.seed)
    print(
        f"generated {len(manifest['files'])} documents in {args.out / 'docs'} "
        f"and {len(manifest['queries'])} queries in {args.out / 'queries.json'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
