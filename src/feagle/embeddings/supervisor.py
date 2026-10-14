"""Lifecycle management for a local ``llama-server`` embeddings instance.

Coordination between processes happens through ``llama.port`` / ``llama.pid`` in
the runtime directory: whoever needs the server first either reuses a healthy
one or spawns a new one and becomes its owner. The owner stops it after
``idle_timeout_min`` minutes without requests. When ``server_url`` is set, feagle
neither spawns nor stops anything.
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .. import paths
from ..log import get_logger
from .client import ServerUnavailable

log = get_logger("embeddings.supervisor")


@dataclass
class SupervisorConfig:
    """Everything needed to launch ``llama-server``."""

    model_path: Path
    server_url: str = ""
    idle_timeout_min: float = 10.0
    threads: int = 0
    context: int = 4096
    slots: int = 8
    batch: int = 4096
    ubatch: int = 4096
    extra_args: list[str] = field(default_factory=list)
    llama_server: str = "llama-server"
    health_timeout: float = 60.0
    alias: str = "feagle"


def build_llama_command(cfg: SupervisorConfig, port: int) -> list[str]:
    """Assemble the ``llama-server`` argv (RESEARCH §4.1)."""
    command = [
        cfg.llama_server,
        "-m", str(cfg.model_path),
        "--embeddings",
        "--host", "127.0.0.1",
        "--port", str(port),
        "--no-webui",
        "--cache-ram", "0",
        "--alias", cfg.alias,
        "-c", str(cfg.context),
        "-np", str(cfg.slots),
        "-b", str(cfg.batch),
        "-ub", str(cfg.ubatch),
    ]
    if cfg.threads > 0:
        command += ["-t", str(cfg.threads)]
    # NOTE: never pass --pooling; mean pooling is baked into the GGUF (§12.2).
    command += list(cfg.extra_args)
    return command


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _default_health(url: str, timeout: float = 1.0) -> bool:
    try:
        response = httpx.get(f"{url}/health", timeout=timeout)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


class LlamaSupervisor:
    """Owns (or reuses) one ``llama-server`` process."""

    def __init__(
        self,
        cfg: SupervisorConfig,
        *,
        port_file: Path | None = None,
        pid_file: Path | None = None,
        log_file: Path | None = None,
        spawn: Callable[[list[str]], Any] | None = None,
        health: Callable[[str], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cfg = cfg
        self.port_file = port_file or (paths.runtime_subdir() / "llama.port")
        self.pid_file = pid_file or (paths.runtime_subdir() / "llama.pid")
        self.log_file = log_file or (paths.state_dir() / "llama.log")
        self._spawn = spawn or self._default_spawn
        self._health = health or _default_health
        self._clock = clock
        self._sleep = sleep
        self._proc: Any | None = None
        self._owned = False
        self._url: str | None = None
        self._last_activity = self._clock()

    # -- process helpers ---------------------------------------------------

    def _default_spawn(self, command: list[str]) -> Any:  # pragma: no cover - real spawn
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        log_handle = open(self.log_file, "ab")  # noqa: SIM115 - kept open for the process lifetime
        try:
            return subprocess.Popen(
                command, stdout=log_handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL
            )
        finally:
            log_handle.close()

    # -- files -------------------------------------------------------------

    def _read_port(self) -> int | None:
        try:
            text = self.port_file.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        try:
            port = int(text)
        except ValueError:
            return None
        return port if 0 < port < 65536 else None

    def _write_files(self, port: int, pid: int) -> None:
        self.port_file.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self.port_file.parent, 0o700)
        self.port_file.write_text(str(port), encoding="utf-8")
        self.pid_file.write_text(str(pid), encoding="utf-8")

    def _remove_files(self) -> None:
        self.port_file.unlink(missing_ok=True)
        self.pid_file.unlink(missing_ok=True)

    # -- public API --------------------------------------------------------

    @property
    def url(self) -> str | None:
        return self._url

    @property
    def owned(self) -> bool:
        return self._owned

    def ensure_running(self) -> str:
        """Return a base URL for a healthy server, spawning one if needed."""
        if self.cfg.server_url:
            self._url = self.cfg.server_url.rstrip("/")
            self._owned = False
            return self._url

        port = self._read_port()
        if port is not None:
            url = f"http://127.0.0.1:{port}"
            if self._health(url):
                log.debug("reusing llama-server at %s", url)
                self._url = url
                self._owned = False
                self._last_activity = self._clock()
                return url
            log.warning("stale llama-server port file; starting a new server")
            self._remove_files()

        port = _free_port()
        url = f"http://127.0.0.1:{port}"
        command = build_llama_command(self.cfg, port)
        log.info("starting llama-server on port %d", port)
        self._proc = self._spawn(command)
        self._write_files(port, int(getattr(self._proc, "pid", 0)))

        if self._wait_healthy(url):
            self._url = url
            self._owned = True
            self._last_activity = self._clock()
            return url

        self._remove_files()
        self._terminate()
        raise ServerUnavailable(
            f"llama-server did not become healthy in {self.cfg.health_timeout:.0f}s; "
            f"see {self.log_file}"
        )

    def _wait_healthy(self, url: str) -> bool:
        deadline = self._clock() + self.cfg.health_timeout
        while self._clock() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                log.error("llama-server exited early with code %s", self._proc.returncode)
                return False
            if self._health(url):
                return True
            self._sleep(0.5)
        return False

    def touch(self) -> None:
        self._last_activity = self._clock()

    def stop_if_idle(self) -> bool:
        """Stop an owned server idle for longer than the configured timeout."""
        if not self._owned or self._proc is None or self.cfg.idle_timeout_min <= 0:
            return False
        if self._clock() - self._last_activity >= self.cfg.idle_timeout_min * 60:
            log.info("stopping idle llama-server")
            self.stop()
            return True
        return False

    def stop(self) -> None:
        self._terminate()
        self._remove_files()
        self._proc = None
        self._owned = False
        self._url = None

    def _terminate(self) -> None:
        proc = self._proc
        if proc is None:
            return
        with contextlib.suppress(Exception):
            proc.terminate()
        with contextlib.suppress(Exception):
            proc.wait(timeout=10)
            return
        with contextlib.suppress(Exception):
            proc.kill()
