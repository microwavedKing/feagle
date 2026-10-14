"""Tests for the minimal .gitignore matcher."""

from __future__ import annotations

from feagle.indexer.gitignore import GitIgnore, matches_chain


def _matcher(tmp_path, patterns):
    return GitIgnore(tmp_path, patterns)


def test_basename_pattern_matches_any_depth(tmp_path):
    gi = _matcher(tmp_path, ["*.log"])
    assert gi.match(tmp_path / "a.log") is True
    assert gi.match(tmp_path / "sub" / "a.log") is True
    assert gi.match(tmp_path / "a.txt") is None


def test_anchored_pattern_only_at_base(tmp_path):
    gi = _matcher(tmp_path, ["/build"])
    assert gi.match(tmp_path / "build", is_dir=True) is True
    assert gi.match(tmp_path / "sub" / "build", is_dir=True) is None


def test_inner_slash_anchors(tmp_path):
    gi = _matcher(tmp_path, ["docs/*.txt"])
    assert gi.match(tmp_path / "docs" / "a.txt") is True
    assert gi.match(tmp_path / "other" / "docs" / "a.txt") is None


def test_dir_only_pattern(tmp_path):
    gi = _matcher(tmp_path, ["cache/"])
    assert gi.match(tmp_path / "cache", is_dir=True) is True
    assert gi.match(tmp_path / "cache", is_dir=False) is None


def test_negation_last_match_wins(tmp_path):
    gi = _matcher(tmp_path, ["*.log", "!keep.log"])
    assert gi.match(tmp_path / "keep.log") is False
    assert gi.match(tmp_path / "drop.log") is True


def test_double_star(tmp_path):
    gi = _matcher(tmp_path, ["**/generated", "logs/**"])
    assert gi.match(tmp_path / "a" / "b" / "generated", is_dir=True) is True
    assert gi.match(tmp_path / "logs" / "x" / "y.txt") is True


def test_character_class_and_question(tmp_path):
    gi = _matcher(tmp_path, ["a?.txt", "[12].md"])
    assert gi.match(tmp_path / "ab.txt") is True
    assert gi.match(tmp_path / "abc.txt") is None
    assert gi.match(tmp_path / "1.md") is True
    assert gi.match(tmp_path / "3.md") is None


def test_comments_and_blanks_ignored(tmp_path):
    gi = _matcher(tmp_path, ["# comment", "", "   "])
    assert gi.rules == []
    assert gi.match(tmp_path / "anything") is None


def test_chain_innermost_wins(tmp_path):
    outer = GitIgnore(tmp_path, ["*.log"])
    inner = GitIgnore(tmp_path / "sub", ["!important.log"])
    chain = [outer, inner]
    assert matches_chain(chain, tmp_path / "sub" / "important.log") is False
    assert matches_chain(chain, tmp_path / "sub" / "other.log") is True


def test_path_outside_base_is_not_matched(tmp_path):
    gi = GitIgnore(tmp_path / "sub", ["*.log"])
    assert gi.match(tmp_path / "a.log") is None
