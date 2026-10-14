"""``feagle doctor``: check the environment and say how to fix problems.

Every check returns a :class:`Check` with a status (``ok`` / ``warn`` / ``fail`` /
``info``), a message and, when something is wrong, a concrete fix hint. The
checks never raise: a broken environment is reported, not crashed on.
"""

from __future__ import annotations

import contextlib
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import db, paths, units
from .embeddings.client import EmbeddingClient
from .embeddings.service import resolve_model
from .indexer.extract import pdftotext_available
from .log import get_logger

log = get_logger("doctor")

#: Minimum llama.cpp build with EmbeddingGemma 2 support (2026-10-06, PR #30054).
REQUIRED_LLAMA_BUILD = 11452

STATUS_ORDER = {"fail": 0, "warn": 1, "ok": 2, "info": 3}


@dataclass
class Check:
    name: str
    status: str
    message: str
    hint: str = ""


def _which(name: str) -> str | None:
    return shutil.which(name)


def check_python(version: tuple[int, ...] | None = None) -> Check:
    current = version or sys.version_info[:3]
    if current >= (3, 12):
        return Check("python", "ok", ".".join(map(str, current)))
    return Check("python", "fail", ".".join(map(str, current)), "feagle needs Python 3.12+")


def check_config(cfg: dict[str, Any]) -> Check:
    path = paths.config_file()
    origin = path if path.exists() else "built-in defaults"
    roots = cfg["index"]["roots"]
    if not roots:
        return Check(
            "config", "warn", f"{origin}: no roots configured",
            "run 'feagle setup' or set [index].roots",
        )
    return Check("config", "ok", f"{origin}: {len(roots)} root(s)")


def check_xdg() -> Check:
    missing = [
        name
        for name, value in (
            ("runtime", paths.runtime_dir()),
            ("data", paths.data_dir().parent),
            ("config", paths.config_dir().parent),
        )
        if not value.exists()
    ]
    if missing:
        return Check(
            "xdg", "warn", "missing: " + ", ".join(missing),
            "these are created on demand; XDG_RUNTIME_DIR matters most",
        )
    return Check("xdg", "ok", "config, data and runtime directories exist")


def check_fts5() -> Check:
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        conn.close()
    except sqlite3.Error as exc:
        return Check("fts5", "fail", str(exc), "sqlite3 must be built with FTS5")
    return Check("fts5", "ok", f"sqlite {sqlite3.sqlite_version}")


def check_sqlite_vec() -> Check:
    try:
        conn = sqlite3.connect(":memory:")
        if not db.load_sqlite_vec(conn):
            return Check(
                "sqlite-vec", "fail", "extension not loadable",
                "pip install sqlite-vec",
            )
        version = conn.execute("SELECT vec_version()").fetchone()[0]
        conn.close()
    except sqlite3.Error as exc:
        return Check("sqlite-vec", "fail", str(exc), "pip install sqlite-vec")
    return Check("sqlite-vec", "ok", str(version))


def check_database() -> Check:
    db_path = paths.db_path()
    if not db_path.exists():
        return Check("database", "warn", f"not created yet ({db_path})", "run 'feagle index'")
    try:
        conn = db.connect(db_path, read_only=True)
        try:
            version = db.get_schema_version(conn)
            dim = db.get_embedding_dim(conn)
            chunks = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
        finally:
            conn.close()
    except (sqlite3.Error, OSError) as exc:
        return Check("database", "fail", str(exc), "check file permissions")
    detail = f"schema v{version}, {chunks} chunks"
    if dim:
        detail += f", dim {dim}"
    return Check("database", "ok", detail)


def check_systemd_units() -> Check:
    if not units.are_installed():
        return Check(
            "units", "warn", "user units not installed",
            "run 'feagle setup' (or copy systemd/*.service to "
            "~/.config/systemd/user)",
        )
    if _which("systemctl") is None:
        return Check("units", "warn", "installed, but systemctl not found", "")
    order = ["feagle-queryd.service", "feagle-indexerd.service"]
    states = []
    for name in order:
        try:
            proc = subprocess.run(
                ["systemctl", "--user", "is-active", name],
                capture_output=True, text=True, timeout=5, check=False,
            )
            states.append(f"{name.split('.')[0]}={proc.stdout.strip() or 'unknown'}")
        except (OSError, subprocess.TimeoutExpired):
            states.append(f"{name.split('.')[0]}=unknown")
    return Check("units", "ok", "installed; " + ", ".join(states))


def check_queryd() -> Check:
    from .search.queryd import QuerydClient

    if QuerydClient().is_running():
        return Check("queryd", "ok", f"socket {paths.socket_path()}")
    return Check(
        "queryd", "warn", "not running",
        "systemctl --user enable --now feagle-queryd (search works without it)",
    )


def _server_base_url(cfg: dict[str, Any]) -> str | None:
    url = cfg["embeddings"]["server_url"]
    if url:
        return url.rstrip("/")
    with contextlib.suppress(OSError, ValueError):
        port = int((paths.runtime_subdir() / "llama.port").read_text().strip())
        return f"http://127.0.0.1:{port}"
    return None


def check_llama_server() -> Check:
    binary = _which("llama-server")
    if binary is None:
        return Check(
            "llama-server", "warn", "not installed (full-text search only)",
            "install llama.cpp (AUR 'llama.cpp-git'; needs a build newer than 2026-10-06)",
        )
    try:
        proc = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=5, check=False
        )
        text = (proc.stdout + proc.stderr).strip().splitlines()[:2]
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Check("llama-server", "warn", f"installed but unusable: {exc}", "")
    version = " ".join(text)
    if f"build {REQUIRED_LLAMA_BUILD}" in version or _build_number(version) >= REQUIRED_LLAMA_BUILD:
        return Check("llama-server", "ok", version)
    return Check(
        "llama-server", "warn", version,
        f"EmbeddingGemma 2 needs build >= {REQUIRED_LLAMA_BUILD}; update llama.cpp",
    )


def _build_number(text: str) -> int:
    import re

    match = re.search(r"build\s+(\d+)", text)
    return int(match.group(1)) if match else 0


def check_model(cfg: dict[str, Any], *, verify_hash: bool = True) -> Check:
    from .embeddings.download import sha256_file

    path, spec = resolve_model(cfg)
    if not path.exists():
        return Check(
            "model", "warn", f"not found ({path})",
            "run 'feagle setup' to download it, or set [embeddings].model",
        )
    if spec is None:
        return Check("model", "ok", f"{path} ({path.stat().st_size} bytes)")
    if path.stat().st_size != spec.size:
        return Check(
            "model", "fail", f"{path}: size {path.stat().st_size} != {spec.size}",
            "re-download the model ('feagle setup')",
        )
    if verify_hash and sha256_file(path) != spec.sha256:
        return Check(
            "model", "fail", f"{path}: sha256 mismatch",
            "re-download the model ('feagle setup')",
        )
    return Check("model", "ok", f"{spec.name} ({path.name})")


def check_server_health(cfg: dict[str, Any]) -> Check:
    url = _server_base_url(cfg)
    if url is None:
        return Check("server", "info", "not running (started on demand)")
    with EmbeddingClient(url, timeout=3.0) as client:
        if client.health(timeout=2.0):
            return Check("server", "ok", f"healthy at {url}")
    return Check("server", "warn", f"unreachable at {url}", "the server is restarted on demand")


def check_embedding_dim(cfg: dict[str, Any]) -> Check:
    db_path = paths.db_path()
    if not db_path.exists():
        return Check("embedding dim", "info", "no index yet")
    conn = db.connect(db_path, read_only=True)
    try:
        stored = db.get_embedding_dim(conn)
    finally:
        conn.close()
    if not stored:
        return Check("embedding dim", "info", "no vectors stored yet")
    url = _server_base_url(cfg)
    if url is None:
        return Check("embedding dim", "info", f"stored {stored}; server not running")
    with EmbeddingClient(url, timeout=10.0) as client:
        if not client.health(timeout=2.0):
            return Check("embedding dim", "info", f"stored {stored}; server not running")
        try:
            live = client.discover_dim()
        except Exception as exc:  # noqa: BLE001
            return Check("embedding dim", "warn", f"cannot probe server: {exc}", "")
    if live != stored:
        return Check(
            "embedding dim", "fail", f"index {stored} vs model {live}",
            "run 'feagle index --rebuild'",
        )
    return Check("embedding dim", "ok", str(stored))


def check_pdftotext() -> Check:
    if pdftotext_available():
        return Check("pdftotext", "ok", _which("pdftotext") or "")
    return Check("pdftotext", "warn", "not installed (PDFs are skipped)",
                 "sudo pacman -S poppler")


def check_ocr(cfg: dict[str, Any]) -> Check:
    from .indexer import ocr

    pdf_enabled = bool(cfg["index"]["ocr"])
    images_enabled = bool(cfg["index"]["ocr_images"])
    enabled = pdf_enabled or images_enabled
    tesseract = ocr.tesseract_available()
    pdftoppm = ocr.pdftoppm_available()

    if not tesseract:
        missing = "tesseract" + ("" if pdftoppm else ", pdftoppm")
        status = "warn" if enabled else "info"
        return Check(
            "ocr", status, f"not available (missing: {missing})",
            "sudo pacman -S tesseract tesseract-data-rus tesseract-data-eng",
        )

    langs = ocr.tesseract_languages()
    wanted = [code for code in cfg["index"]["ocr_langs"].split("+") if code]
    missing_langs = [code for code in wanted if code not in langs]
    detail = "available" + ("" if enabled else " (disabled in config)")
    if missing_langs:
        return Check(
            "ocr", "warn", f"{detail}; missing languages: {', '.join(missing_langs)}",
            "install tesseract-data-" + "/tesseract-data-".join(missing_langs),
        )
    if pdf_enabled and not pdftoppm:
        # Images work with tesseract alone; scanned PDF pages also need poppler.
        return Check(
            "ocr", "warn", f"{detail}; pdftoppm missing (PDF pages are skipped)",
            "sudo pacman -S poppler",
        )
    return Check("ocr", "ok", f"{detail}; languages: {', '.join(wanted)}")


def check_launcher(cfg: dict[str, Any]) -> Check:
    program = cfg["launcher"]["program"]
    candidates = ("fuzzel", "wofi", "rofi", "tofi", "walker")
    available = [name for name in candidates if _which(name)]
    if program != "auto":
        if program == "stdout" or _which(program):
            return Check("launcher", "ok", program)
        return Check("launcher", "warn", f"{program} not found", f"install {program}")
    if available:
        return Check("launcher", "ok", "auto: " + ", ".join(available))
    return Check("launcher", "warn", "no launcher found (stdout fallback)",
                 "install fuzzel/wofi/rofi/tofi/walker")


def _is_battery(entry: Path) -> bool:
    """Whether a ``/sys/class/power_supply`` entry is a battery.

    The directory name is not reliable (``BAT0``, but also ``CMB0``, ``BATT`` or
    ``macsmc-battery``), so the ``type`` file is the source of truth.
    """
    try:
        return (entry / "type").read_text(encoding="utf-8").strip() == "Battery"
    except OSError:
        return False


def check_power(power_supply_dir: Path | None = None) -> Check:
    from .power import POWER_SUPPLY_DIR, on_battery

    directory = power_supply_dir or POWER_SUPPLY_DIR
    has_battery = directory.is_dir() and any(
        _is_battery(entry) for entry in directory.iterdir()
    )
    if has_battery:
        state = "on battery" if on_battery(directory) else "on AC"
        return Check("battery", "info", f"present ({state})")
    return Check("battery", "info", "none (desktop; pause never triggers)")


def check_inotify(path: Path = Path("/proc/sys/fs/inotify/max_user_watches")) -> Check:
    try:
        watches = int(path.read_text().strip())
    except (OSError, ValueError):
        return Check("inotify", "info", "limit unavailable")
    if watches >= 65536:
        return Check("inotify", "ok", f"max_user_watches={watches}")
    return Check(
        "inotify", "warn", f"max_user_watches={watches} (low)",
        "raise fs.inotify.max_user_watches or use 'feagle indexerd --poll'",
    )


def check_cgroup(path: Path = Path("/sys/fs/cgroup")) -> Check:
    try:
        fstype = subprocess.run(
            ["stat", "-fc", "%T", str(path)], capture_output=True, text=True, timeout=5, check=False
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return Check("cgroup", "info", "cannot determine")
    if fstype == "cgroup2fs":
        return Check("cgroup", "ok", "cgroup v2 (resource limits work)")
    return Check("cgroup", "warn", f"fstype {fstype!r}",
                 "Nice/IOScheduling CPUQuota need cgroup v2 unified")


def check_disk(min_free_mb: int = 512) -> Check:
    target = paths.data_dir().parent
    while not target.exists() and target != target.parent:
        target = target.parent
    usage = shutil.disk_usage(target)
    free_mb = usage.free // (1024 * 1024)
    if free_mb >= min_free_mb:
        return Check("disk", "ok", f"{free_mb} MiB free")
    return Check("disk", "warn", f"only {free_mb} MiB free", "free up disk space")


def run_checks(cfg: dict[str, Any], *, verify_model_hash: bool = True) -> list[Check]:
    checks = [
        check_python(),
        check_config(cfg),
        check_xdg(),
        check_fts5(),
        check_sqlite_vec(),
        check_database(),
        check_systemd_units(),
        check_queryd(),
        check_llama_server(),
        check_model(cfg, verify_hash=verify_model_hash),
        check_server_health(cfg),
        check_embedding_dim(cfg),
        check_pdftotext(),
        check_ocr(cfg),
        check_launcher(cfg),
        check_power(),
        check_inotify(),
        check_cgroup(),
        check_disk(),
    ]
    return checks
