#!/usr/bin/env python3
"""Keep the release version in one place and pin the tag archive checksum.

The single source of truth is ``src/feagle/__init__.py`` (``__version__``);
``pyproject.toml`` reads it through hatchling. A few places cannot read it —
``PKGBUILD``, ``.SRCINFO`` and the examples in the documentation — so this
script keeps them in sync instead of relying on memory.

Nothing here tags, commits or pushes: it only edits files and prints the git
commands to run.

Usage:
    scripts/release.py bump 0.3.0   # set the version everywhere, refresh .SRCINFO
    scripts/release.py sum 0.3.0    # download the vX.Y.Z archive and pin its sha256
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

ROOT = Path(__file__).resolve().parent.parent
INIT = Path("src/feagle/__init__.py")
PKGBUILD = Path("PKGBUILD")
SRCINFO = Path(".SRCINFO")
DOCS = ("README.md", "ARCHITECTURE.md", "quickshell/README.md", "man/feagle.1")
ARCHIVE_URL = "https://github.com/microwavedKing/feagle/archive/refs/tags/v{version}.tar.gz"

_VERSION_RE = re.compile(r'(?m)^__version__ = "([^"]+)"$')
_PKGVER_RE = re.compile(r"(?m)^pkgver=.*$")
_SHA_RE = re.compile(r"(?m)^sha256sums=\('[^']*'\)$")
_STATUS_RE = re.compile(r"(?m)^(> \*\*(?:Status|Статус):\*\*[^`\n]*`)[^`]+(`)")
_PING_RE = re.compile(r'"version":"[^"]+"')
_TH_RE = re.compile(r'(?m)^\.TH FEAGLE 1 "[^"]*" "feagle [^"]*"')


def read_version(root: Path = ROOT) -> str:
    """The version from ``src/feagle/__init__.py`` (the single source of truth)."""
    match = _VERSION_RE.search((root / INIT).read_text(encoding="utf-8"))
    if match is None:
        raise SystemExit(f"no __version__ found in {INIT}")
    return match.group(1)


def _sub_once(pattern: re.Pattern[str], replacement: str, text: str, what: str) -> str:
    """Substitute exactly once, or fail loudly (a silently missed copy is a bug)."""
    new_text, count = pattern.subn(replacement, text)
    if count != 1:
        raise SystemExit(f"expected exactly one {what} to replace, found {count}")
    return new_text


def set_init_version(text: str, version: str) -> str:
    return _sub_once(_VERSION_RE, f'__version__ = "{version}"', text, "__version__")


def set_pkgver(text: str, version: str) -> str:
    return _sub_once(_PKGVER_RE, f"pkgver={version}", text, "pkgver")


def set_sha256(text: str, digest: str) -> str:
    return _sub_once(_SHA_RE, f"sha256sums=('{digest}')", text, "sha256sums")


def set_doc_versions(text: str, version: str, stamp: str | None = None) -> str:
    """Update the status line, any ``ping`` example and the man page header.

    All three are optional: a file simply does not have to contain them. The man
    page's ``.TH`` line takes the release date (today, unless given).
    """
    text = _STATUS_RE.sub(rf"\g<1>{version}\g<2>", text)
    text = _PING_RE.sub(f'"version":"{version}"', text)
    if _TH_RE.search(text):
        when = stamp or date.today().isoformat()
        text = _TH_RE.sub(f'.TH FEAGLE 1 "{when}" "feagle {version}"', text)
    return text


def write_srcinfo(root: Path = ROOT) -> None:
    """Regenerate ``.SRCINFO`` from ``PKGBUILD`` (needs makepkg)."""
    result = subprocess.run(
        ["makepkg", "--printsrcinfo"], cwd=root, capture_output=True, text=True, check=True
    )
    (root / SRCINFO).write_text(result.stdout, encoding="utf-8")


def bump(root: Path, version: str, *, regenerate: bool = True) -> list[str]:
    """Write ``version`` into every tracked place; return the changed paths."""
    changed: list[str] = []

    def update(relative: Path, transform: Callable[[str], str]) -> None:
        path = root / relative
        text = path.read_text(encoding="utf-8")
        new_text = transform(text)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            changed.append(str(relative))

    update(INIT, lambda text: set_init_version(text, version))
    update(PKGBUILD, lambda text: set_pkgver(text, version))
    for doc in DOCS:
        update(Path(doc), lambda text: set_doc_versions(text, version))
    if regenerate:
        write_srcinfo(root)
    return changed


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def pin_sum(root: Path, version: str, *, regenerate: bool = True) -> str:
    """Download the tag archive, pin its sha256 in ``PKGBUILD``; return the digest."""
    url = ARCHIVE_URL.format(version=version)
    with tempfile.TemporaryDirectory(prefix="feagle-release-") as tmp:
        archive = Path(tmp) / f"feagle-{version}.tar.gz"
        with urllib.request.urlopen(url) as response, open(archive, "wb") as out:
            shutil.copyfileobj(response, out)
        digest = sha256_of(archive)
    text = (root / PKGBUILD).read_text(encoding="utf-8")
    (root / PKGBUILD).write_text(set_sha256(text, digest), encoding="utf-8")
    if regenerate:
        write_srcinfo(root)
    return digest


def _bump_command(args: argparse.Namespace) -> int:
    changed = bump(ROOT, args.version)
    print(f"version {args.version} written to: {', '.join(changed) or '(nothing changed)'}")
    print(
        "\nnext:\n"
        f"  git add -A && git commit -m 'release: v{args.version}'\n"
        f"  git tag -a v{args.version} -m 'feagle {args.version}'\n"
        "  git push origin main && git push origin v" + args.version + "\n"
        f"  scripts/release.py sum {args.version}"
    )
    return 0


def _sum_command(args: argparse.Namespace) -> int:
    digest = pin_sum(ROOT, args.version)
    print(f"sha256sums pinned to {digest}")
    print(
        "\nnext:\n"
        "  makepkg --verifysource\n"
        "  git add PKGBUILD .SRCINFO && git commit -m "
        f"'pkg: pin the v{args.version} tag archive checksum'"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    bump_parser = sub.add_parser("bump", help="set the version in every tracked place")
    bump_parser.add_argument("version", help="new version, e.g. 0.3.0")
    bump_parser.set_defaults(func=_bump_command)

    sum_parser = sub.add_parser("sum", help="pin the tag archive checksum")
    sum_parser.add_argument("version", help="released version, e.g. 0.3.0")
    sum_parser.set_defaults(func=_sum_command)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
