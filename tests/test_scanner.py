"""Tests for the filesystem scanner."""

from __future__ import annotations

from feagle.indexer.scanner import Scanner


def _names(candidates):
    return sorted(c.path.name for c in candidates)


def _scanner(**overrides):
    params = {
        "include_ext": [".txt", ".md", ".pdf"],
        "exclude_dirs": ["node_modules"],
        "use_gitignore": True,
        "max_file_size_mb": 10,
        "max_pdf_file_size_mb": 50,
        "follow_symlinks": False,
    }
    params.update(overrides)
    return Scanner(**params)


def _make_tree(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "b.md").write_text("world", encoding="utf-8")
    (tmp_path / "c.bin").write_text("binary", encoding="utf-8")
    (tmp_path / ".hidden.txt").write_text("hidden", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "d.txt").write_text("nested", encoding="utf-8")
    nm = tmp_path / "node_modules"
    nm.mkdir()
    (nm / "e.txt").write_text("vendor", encoding="utf-8")


def test_extension_allowlist_and_hidden(tmp_path):
    _make_tree(tmp_path)
    found = _names(_scanner().iter_files([tmp_path]))
    assert found == ["a.txt", "b.md", "d.txt"]


def test_excluded_dirs(tmp_path):
    _make_tree(tmp_path)
    found = _names(_scanner().iter_files([tmp_path]))
    assert "e.txt" not in found


def test_gitignore_is_respected(tmp_path):
    _make_tree(tmp_path)
    (tmp_path / ".gitignore").write_text("*.md\nsub/\n", encoding="utf-8")
    found = _names(_scanner().iter_files([tmp_path]))
    assert found == ["a.txt"]


def test_gitignore_can_be_disabled(tmp_path):
    _make_tree(tmp_path)
    (tmp_path / ".gitignore").write_text("*.md\n", encoding="utf-8")
    found = _names(_scanner(use_gitignore=False).iter_files([tmp_path]))
    assert "b.md" in found


def test_size_limit(tmp_path):
    _make_tree(tmp_path)
    (tmp_path / "big.txt").write_text("x" * 3000, encoding="utf-8")
    found = _names(_scanner(max_file_size_mb=1 / 1024).iter_files([tmp_path]))
    assert "big.txt" not in found
    assert "big.txt" in _names(_scanner().iter_files([tmp_path]))


def test_pdf_has_separate_size_limit(tmp_path):
    (tmp_path / "doc.pdf").write_bytes(b"%PDF" + b"x" * 3000)
    found = _names(
        _scanner(max_file_size_mb=1 / 1024, max_pdf_file_size_mb=10).iter_files([tmp_path])
    )
    assert found == ["doc.pdf"]


def test_symlinks_are_skipped(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "target.txt").write_text("real", encoding="utf-8")

    root = tmp_path / "root"
    root.mkdir()
    (root / "ok.txt").write_text("ok", encoding="utf-8")
    (root / "link.txt").symlink_to(outside / "target.txt")
    (root / "linkdir").symlink_to(outside, target_is_directory=True)

    found = _names(_scanner().iter_files([root]))
    assert found == ["ok.txt"]


def test_extra_excluded_paths(tmp_path):
    _make_tree(tmp_path)
    data = tmp_path / "sub"
    found = _names(_scanner(exclude_paths=[data]).iter_files([tmp_path]))
    assert "d.txt" not in found


def test_missing_root_is_skipped(tmp_path):
    candidates = list(_scanner().iter_files([tmp_path / "does-not-exist"]))
    assert candidates == []


# ---- candidate_for: one path, same filters ------------------------------


def test_candidate_for_accepts_a_normal_file(tmp_path):
    _make_tree(tmp_path)
    candidate = _scanner().candidate_for(tmp_path / "a.txt")
    assert candidate is not None
    assert candidate.path == tmp_path / "a.txt"
    assert candidate.size == 5


def test_candidate_for_rejects_what_a_walk_would_skip(tmp_path):
    _make_tree(tmp_path)
    scanner = _scanner()
    assert scanner.candidate_for(tmp_path / "c.bin") is None          # extension
    assert scanner.candidate_for(tmp_path / ".hidden.txt") is None    # hidden
    assert scanner.candidate_for(tmp_path / "node_modules" / "e.txt") is None
    assert scanner.candidate_for(tmp_path / "missing.txt") is None
    assert scanner.candidate_for(tmp_path) is None                    # a directory


def test_candidate_for_respects_gitignore_along_the_way(tmp_path):
    _make_tree(tmp_path)
    sub = tmp_path / "sub"
    (sub / ".gitignore").write_text("d.txt\n", encoding="utf-8")
    assert _scanner().candidate_for(sub / "d.txt") is None
    assert _scanner(use_gitignore=False).candidate_for(sub / "d.txt") is not None


def test_candidate_for_respects_size_limits(tmp_path):
    big = tmp_path / "big.txt"
    big.write_text("x" * 2048, encoding="utf-8")
    assert _scanner(max_file_size_mb=0.001).candidate_for(big) is None
    assert _scanner(max_file_size_mb=1).candidate_for(big) is not None
