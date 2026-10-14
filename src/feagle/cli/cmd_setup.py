"""``feagle setup``: interactive first-run configuration.

Steps: choose roots, launcher and model profile; write the config (with a
backup); optionally download the model (SHA256 verified); install and enable the
systemd user units; run the first index. Everything can be driven
non-interactively with flags and ``--yes``.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from typing import Any

from .. import config, db, paths, setup, units
from ..embeddings import presets
from ..embeddings.client import EmbeddingError, ServerUnavailable
from ..embeddings.download import DownloadError, ensure_model, verify_pin
from ..embeddings.service import EmbeddingService, ModelMissingError, resolve_model
from ..indexer import pipeline

_DEGRADE_ERRORS = (ModelMissingError, ServerUnavailable, EmbeddingError, OSError)
LAUNCHER_CHOICES = ("auto", "fuzzel", "wofi", "rofi", "tofi", "walker", "stdout")


def _prompt(text: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{text}{suffix}: ").strip()
    except EOFError:
        print(file=sys.stderr)
        return default
    return answer or default


def _prompt_yes_no(text: str, default: bool = True) -> bool:
    marker = "Y/n" if default else "y/N"
    try:
        answer = input(f"{text} [{marker}]: ").strip().lower()
    except EOFError:
        print(file=sys.stderr)
        return default
    if not answer:
        return default
    return answer in ("y", "yes", "д", "да")


def _detect_launchers() -> list[str]:
    return [name for name in LAUNCHER_CHOICES if name != "auto" and shutil.which(name)]


def _gather_plan(args: argparse.Namespace, cfg: dict[str, Any]) -> setup.SetupPlan:
    plan = setup.SetupPlan(
        roots=list(args.root or []),
        model=args.model,
        mrl_dim=args.mrl_dim,
        launcher=args.launcher,
        download=not args.no_download,
        install_units=not args.no_units,
        enable_units=not args.no_units and not args.no_enable,
        run_index=not args.no_index,
        backup=not args.no_backup,
    )

    if args.yes:
        if not plan.roots:
            plan.roots = cfg["index"]["roots"] or setup.default_roots()
        return plan

    print("feagle setup — press Enter to accept the default in brackets.\n")

    if not plan.roots:
        suggested = " ".join(cfg["index"]["roots"] or setup.default_roots())
        raw = _prompt("Directories to index (space separated)", suggested)
        plan.roots = raw.split()

    if not plan.launcher:
        detected = _detect_launchers()
        suggested = detected[0] if detected else ("auto" if detected else "stdout")
        plan.launcher = _prompt(f"Launcher {LAUNCHER_CHOICES}", suggested).strip()

    if not plan.model:
        names = sorted(presets.PRESETS)
        plan.model = _prompt(f"Model preset {names}", names[0]).strip()

    if plan.mrl_dim is None:
        plan.mrl_dim = int(_prompt(f"Vector size {presets.MRL_DIMS}", "768"))

    plan.download = plan.download and _prompt_yes_no(
        "Download the model now", True
    )
    plan.install_units = _prompt_yes_no("Install systemd user units", True)
    plan.enable_units = plan.install_units and _prompt_yes_no(
        "Enable and start the services now", True
    )
    plan.run_index = _prompt_yes_no("Run the first index now", True)
    return plan


def _download_model(cfg: dict[str, Any]) -> bool:
    model, spec = resolve_model(cfg)
    if spec is None:
        if model.exists():
            print(f"using existing model: {model}")
            return True
        print(f"feagle: model file not found: {model}", file=sys.stderr)
        return False

    paths.ensure_data_dirs()
    try:
        verify_pin(spec)
    except DownloadError as exc:
        print(f"feagle: warning: could not verify the pin ({exc})", file=sys.stderr)

    last = {"mb": -1}

    def progress(done: int, total: int | None) -> None:
        mb = done // (1024 * 1024)
        if mb >= last["mb"] + 50:
            last["mb"] = mb
            pct = f"{done * 100 // total:3d}%" if total else "?"
            print(f"  {mb:5d} MiB {pct}", flush=True)

    try:
        path = ensure_model(spec, paths.models_dir(), progress=progress)
    except DownloadError as exc:
        print(f"feagle: model download failed: {exc}", file=sys.stderr)
        return False
    print(f"model ready: {path}")
    return True


def _install_units() -> None:
    written = units.install()
    print("installed units: " + ", ".join(path.name for path in written))


def _enable_units() -> bool:
    if shutil.which("systemctl") is None:
        print("feagle: systemctl not found; enable the units manually", file=sys.stderr)
        return False
    commands = [
        ["systemctl", "--user", "daemon-reload"],
        [
            "systemctl", "--user", "enable", "--now",
            "feagle-queryd.service", "feagle-indexerd.service",
        ],
    ]
    for command in commands:
        try:
            proc = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"feagle: {' '.join(command)} failed: {exc}", file=sys.stderr)
            return False
        if proc.returncode != 0:
            print(f"feagle: {' '.join(command)}: {proc.stderr.strip()}", file=sys.stderr)
            return False
    print("enabled units: feagle-queryd, feagle-indexerd")
    return True


def _first_index(cfg: dict[str, Any]) -> None:
    service = EmbeddingService(cfg)
    embedder = None
    try:
        embedder = service.start()
    except _DEGRADE_ERRORS as exc:
        print(f"feagle: indexing without embeddings ({exc})", file=sys.stderr)

    conn = db.open_database()
    try:
        stats = pipeline.index_roots(conn, cfg, embedder=embedder)
    finally:
        conn.close()
        service.close()
    print(stats.summary())


def run(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    plan = _gather_plan(args, cfg)
    if not plan.roots and not cfg["index"]["roots"]:
        print(
            "feagle: no directories to index.\n"
            "Pass --root PATH (repeatable) or run 'feagle setup' interactively.",
            file=sys.stderr,
        )
        return 1

    updated = setup.apply_plan(cfg, plan)
    try:
        config.validate(updated)
    except config.ConfigError as exc:
        print(f"feagle: {exc}", file=sys.stderr)
        return 2

    path, backup = setup.write_config(updated, backup=plan.backup)
    print(f"wrote config: {path}" + (f" (backup: {backup})" if backup else ""))

    if plan.download and not updated["embeddings"]["server_url"]:
        _download_model(updated)

    if plan.install_units:
        _install_units()
        if plan.enable_units:
            _enable_units()

    if plan.run_index:
        _first_index(updated)

    print(
        "\nDone. Next: 'feagle search \"your query\"'.\n"
        "Launcher adapters and hotkey bindings ('feagle search --pick') arrive in a later phase."
    )
    return 0
