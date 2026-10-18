"""Tests for scripts/release.py (the version-sync helpers)."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_release():
    spec = importlib.util.spec_from_file_location("feagle_release", ROOT / "scripts" / "release.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = _load_release()


def test_version_has_a_single_source_of_truth():
    from feagle import __version__

    assert release.read_version() == __version__


def test_pyproject_reads_the_version_dynamically():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'dynamic = ["version"]' in text
    assert re.search(r"(?m)^version = ", text) is None


def test_set_init_version():
    assert release.set_init_version('__version__ = "0.1.0"\n', "0.2.0") == '__version__ = "0.2.0"\n'


def test_set_pkgver():
    assert release.set_pkgver("pkgname=feagle\npkgver=0.1.0\n", "0.2.0") == (
        "pkgname=feagle\npkgver=0.2.0\n"
    )


def test_set_sha256():
    text = "sha256sums=('" + "0" * 64 + "')\n"
    assert release.set_sha256(text, "a" * 64) == "sha256sums=('" + "a" * 64 + "')\n"


def test_set_doc_versions_updates_status_and_ping():
    text = (
        "> **Status:** early development (`0.1.0`).\n"
        '{"ok":true,"version":"0.1.0"}\n'
        "> **Статус:** ранняя разработка (`0.1.0`).\n"
    )
    out = release.set_doc_versions(text, "0.2.0")
    assert "(`0.2.0`)" in out
    assert '"version":"0.2.0"' in out
    assert "0.1.0" not in out


def test_set_doc_versions_updates_the_man_page_header():
    text = '.TH FEAGLE 1 "2026-01-02" "feagle 0.1.0"\n'
    out = release.set_doc_versions(text, "0.2.0", stamp="2026-03-04")
    assert out == '.TH FEAGLE 1 "2026-03-04" "feagle 0.2.0"\n'


@pytest.mark.parametrize("pattern", ["__version__", "pkgver", "sha256sums"])
def test_a_missing_target_fails_loudly(pattern):
    with pytest.raises(SystemExit):
        if pattern == "__version__":
            release.set_init_version("nothing here\n", "1.0.0")
        elif pattern == "pkgver":
            release.set_pkgver("nothing here\n", "1.0.0")
        else:
            release.set_sha256("nothing here\n", "0" * 64)


def test_bump_updates_every_copy(tmp_path):
    old = release.read_version()
    copies = [release.INIT, release.PKGBUILD, *(Path(doc) for doc in release.DOCS)]
    for relative in copies:
        dest = tmp_path / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text((ROOT / relative).read_text(encoding="utf-8"), encoding="utf-8")

    changed = release.bump(tmp_path, "9.9.9", regenerate=False)

    assert set(changed) == {str(relative) for relative in copies}
    for relative in copies:
        text = (tmp_path / relative).read_text(encoding="utf-8")
        assert "9.9.9" in text
        assert old not in text
