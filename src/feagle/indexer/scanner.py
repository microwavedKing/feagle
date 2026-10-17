"""Filesystem scanning: walk configured roots and yield indexable files.

Filtering happens here: hidden names, excluded directories, the extension
allowlist, size limits, symlinks, extra excluded paths and ``.gitignore`` files
(one stack per directory, innermost rule wins).
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from ..log import get_logger
from .gitignore import GitIgnore, matches_chain

log = get_logger("indexer.scanner")

_MB = 1024 * 1024


@dataclass(frozen=True)
class ScanCandidate:
    """A file that passed all filters, ready for extraction."""

    path: Path
    size: int
    mtime_ns: int


class Scanner:
    def __init__(
        self,
        *,
        include_ext: Iterable[str],
        exclude_dirs: Iterable[str],
        use_gitignore: bool = True,
        max_file_size_mb: float = 10,
        max_pdf_file_size_mb: float = 50,
        follow_symlinks: bool = False,
        exclude_paths: Iterable[Path] = (),
    ) -> None:
        self.include_ext = {ext.lower() for ext in include_ext}
        self.exclude_dirs = set(exclude_dirs)
        self.use_gitignore = use_gitignore
        self.max_file_bytes = int(max_file_size_mb * _MB)
        self.max_pdf_bytes = int(max_pdf_file_size_mb * _MB)
        self.follow_symlinks = follow_symlinks
        self.exclude_paths = [Path(p) for p in exclude_paths]

    def iter_files(self, roots: Iterable[Path | str]) -> Iterator[ScanCandidate]:
        for root in roots:
            root_path = Path(os.path.abspath(os.path.expanduser(str(root))))
            if not root_path.is_dir():
                log.warning("skipping non-directory root: %s", root_path)
                continue
            yield from self._walk(root_path)

    def _walk(self, root: Path) -> Iterator[ScanCandidate]:
        chains: dict[str, list[GitIgnore]] = {}
        for dirpath, dirnames, filenames in os.walk(
            root, topdown=True, followlinks=self.follow_symlinks
        ):
            here = Path(dirpath)
            chain = chains.get(dirpath, [])
            if self.use_gitignore:
                ignore_file = here / ".gitignore"
                if ignore_file.is_file():
                    chain = [*chain, GitIgnore.from_file(ignore_file)]

            kept = [
                name
                for name in sorted(dirnames)
                if not self._skip_dir(here, name, chain)
            ]
            dirnames[:] = kept
            for name in kept:
                chains[str(here / name)] = chain

            for name in sorted(filenames):
                candidate = self._candidate(here, name, chain)
                if candidate is not None:
                    yield candidate

    def candidate_for(self, path: Path | str) -> ScanCandidate | None:
        """A candidate for one *specific* path (a watch event), or ``None``.

        Applies the same filters as a walk — hidden names, excluded directories,
        the extension allowlist, ``.gitignore`` rules along the way, symlinks and
        the size limits — so targeted re-indexing never indexes something the
        scanner would have skipped.
        """
        target = Path(os.path.abspath(os.path.expanduser(str(path))))
        if target.name.startswith("."):
            return None
        if target.is_symlink() and not self.follow_symlinks:
            return None
        ext = target.suffix.lower()
        if ext not in self.include_ext:
            return None
        if self._excluded(target):
            return None
        if any(
            part.startswith(".") or part in self.exclude_dirs
            for part in target.parent.parts
        ):
            return None
        if self.use_gitignore and self._gitignored(target):
            return None

        try:
            info = target.stat()
        except OSError as exc:
            log.warning("cannot stat %s: %s", target, exc)
            return None
        if not stat.S_ISREG(info.st_mode):
            return None
        limit = self.max_pdf_bytes if ext == ".pdf" else self.max_file_bytes
        if info.st_size > limit:
            log.debug("skipping oversized file (%d bytes): %s", info.st_size, target)
            return None
        return ScanCandidate(path=target, size=info.st_size, mtime_ns=info.st_mtime_ns)

    def _gitignored(self, path: Path) -> bool:
        """``.gitignore`` rules along ``path``'s ancestry, innermost last."""
        chain: list[GitIgnore] = []
        for directory in reversed([path.parent, *path.parent.parents]):
            ignore_file = directory / ".gitignore"
            if ignore_file.is_file():
                chain.append(GitIgnore.from_file(ignore_file))
        return matches_chain(chain, path, is_dir=False)

    def _excluded(self, path: Path) -> bool:
        return any(path == blocked or blocked in path.parents for blocked in self.exclude_paths)

    def _skip_dir(self, parent: Path, name: str, chain: list[GitIgnore]) -> bool:
        child = parent / name
        if child.is_symlink() and not self.follow_symlinks:
            return True
        if name.startswith(".") or name in self.exclude_dirs:
            return True
        if self._excluded(child):
            return True
        return self.use_gitignore and matches_chain(chain, child, is_dir=True)

    def _candidate(
        self, parent: Path, name: str, chain: list[GitIgnore]
    ) -> ScanCandidate | None:
        if name.startswith("."):
            return None
        path = parent / name
        if path.is_symlink() and not self.follow_symlinks:
            return None
        ext = path.suffix.lower()
        if ext not in self.include_ext:
            return None
        if self._excluded(path):
            return None
        if self.use_gitignore and matches_chain(chain, path, is_dir=False):
            return None

        try:
            info = path.stat()
        except OSError as exc:
            log.warning("cannot stat %s: %s", path, exc)
            return None
        if not stat.S_ISREG(info.st_mode):
            return None
        limit = self.max_pdf_bytes if ext == ".pdf" else self.max_file_bytes
        if info.st_size > limit:
            log.debug("skipping oversized file (%d bytes): %s", info.st_size, path)
            return None
        return ScanCandidate(path=path, size=info.st_size, mtime_ns=info.st_mtime_ns)
