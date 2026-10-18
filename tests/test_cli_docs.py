"""The man page and the shell completions must not drift from the CLI parser."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from feagle import __version__
from feagle.cli.__main__ import build_parser

ROOT = Path(__file__).resolve().parent.parent
MAN = ROOT / "man" / "feagle.1"
COMPLETIONS = {
    "bash": ROOT / "completions" / "feagle.bash",
    "zsh": ROOT / "completions" / "_feagle",
    "fish": ROOT / "completions" / "feagle.fish",
}
#: `-h/--help` is universal and documented once, so it is not repeated per command.
_UNIVERSAL = frozenset({"-h", "--help"})

EXPECTED_COMMANDS = {
    "index",
    "search",
    "status",
    "doctor",
    "setup",
    "open",
    "pick",
    "queryd",
    "indexerd",
    "bench",
}


def _subcommands() -> dict[str, argparse.ArgumentParser]:
    action = next(
        a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction)
    )
    return dict(action.choices)


def _options(parser: argparse.ArgumentParser) -> list[str]:
    return [
        option
        for action in parser._actions
        for option in action.option_strings
        if option not in _UNIVERSAL
    ]


def test_the_snapshot_lists_the_real_commands():
    assert set(_subcommands()) == EXPECTED_COMMANDS


def _mentions(shell: str, text: str, option: str) -> bool:
    """Whether a completion file mentions ``option`` (fish spells them ``-l foo``)."""
    if shell == "fish":
        name = option.lstrip("-")
        return f"-l {name} " in text or f"-l {name}\n" in text
    return option in text


@pytest.mark.parametrize("shell", sorted(COMPLETIONS))
def test_completions_list_every_command_and_option(shell):
    text = COMPLETIONS[shell].read_text(encoding="utf-8")
    for name, parser in _subcommands().items():
        assert name in text, f"{shell}: command {name!r} is missing"
        for option in _options(parser):
            assert _mentions(shell, text, option), f"{shell}: {name} option {option!r} is missing"


def test_man_page_documents_every_command_and_option():
    text = MAN.read_text(encoding="utf-8").replace("\\-", "-")
    for name, parser in _subcommands().items():
        assert f".SS {name}" in text, f"man page: command {name!r} is missing"
        for option in _options(parser):
            assert option in text, f"man page: {name} option {option!r} is missing"


def test_man_page_documents_the_global_options():
    text = MAN.read_text(encoding="utf-8").replace("\\-", "-")
    assert "--version" in text
    assert "--help" in text


def test_man_page_version_matches_the_package():
    header = next(
        line for line in MAN.read_text(encoding="utf-8").splitlines() if line.startswith(".TH")
    )
    assert f'"feagle {__version__}"' in header


def test_completions_are_installed_by_the_package():
    """A completion file the package does not install is dead weight."""
    pkgbuild = (ROOT / "PKGBUILD").read_text(encoding="utf-8")
    assert "man/feagle.1" in pkgbuild
    for relative in ("completions/feagle.bash", "completions/_feagle", "completions/feagle.fish"):
        assert relative in pkgbuild
