"""Lifecycle management for a local ``llama-server`` embeddings instance.

Coordination between processes happens through ``llama.port`` / ``llama.pid`` in
the runtime directory: whoever needs the server first either reuses a healthy
one or spawns a new one and becomes its owner. The owner stops it after
``idle_timeout_min`` minutes without requests. When ``server_url`` is set, feagle
neither spawns nor stops anything.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import socket
import subprocess
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from .. import paths
from ..log import get_logger
from .client import ServerUnavailable

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

log = get_logger("embeddings.supervisor")


@dataclass
class SupervisorConfig:
    """Everything needed to launch ``llama-server``."""

    model_path: Path
    server_url: str = ""
    idle_timeout_min: float = 10.0
    threads: int = 0
    #: Optional multimodal projector (``--mmproj``) for image embeddings. Only
    #: set when media image indexing is enabled in the config.
    mmproj_path: Path | None = None
    #: ``True`` for the media server (image/video embeddings, ``--mmproj``).
    #: Media and text servers use separate coordination files so the query daemon
    #: can keep a light text-only server while the indexer runs the media one.
    media: bool = False
    #: Total context and parallel slots. Each slot gets context/slots tokens,
    #: and an embeddings input must fit in one slot (see RESEARCH §4.1), so keep
    #: the per-slot size comfortably above the largest chunk (~2200 chars).
    context: int = 8192
    slots: int = 4
    batch: int = 4096
    ubatch: int = 4096
    extra_args: list[str] = field(default_factory=list)
    #: ``--pooling`` for models that need it. EmbeddingGemma has mean pooling
    #: baked into the GGUF (never pass it); Qwen3-Embedding needs ``last``.
    pooling: str | None = None
    llama_server: str = "llama-server"
    health_timeout: float = 60.0
    #: How long to wait for an already-listed server to become healthy before
    #: declaring it stale. Prevents two daemons from each spawning a server when
    #: they start at the same time.
    startup_grace: float = 15.0
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
    if cfg.mmproj_path is not None:
        command += ["--mmproj", str(cfg.mmproj_path)]
    # Only pass --pooling when the model needs it: EmbeddingGemma has mean pooling
    # baked into the GGUF, while Qwen3-Embedding requires `last` (RESEARCH §4.2).
    if cfg.pooling:
        command += ["--pooling", cfg.pooling]
    command += list(cfg.extra_args)
    return command


def _free_port() -> int:
    """A currently free loopback port.

    The socket is closed by the time the caller uses the number, so the port can
    be taken in between; ``LlamaSupervisor._start_server`` retries on another
    port when the server dies at startup, which is what that race looks like.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


#: Attempts at a fresh port when llama-server exits during startup (see
#: ``_free_port``: the port can be taken between the probe and the bind).
_SPAWN_ATTEMPTS = 3

#: How long SIGTERM gets before SIGKILL (and how long the reap may take).
_TERMINATE_TIMEOUT_S = 10.0


def _default_health(url: str, timeout: float = 1.0) -> bool:
    try:
        response = httpx.get(f"{url}/health", timeout=timeout)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def _process_alive(pid: int) -> bool:
    """Whether ``pid`` is a live process (a zombie counts as dead)."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            state = handle.read().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return False
    return state != "Z"


class LlamaSupervisor:
    """Owns (or reuses) one ``llama-server`` process."""

    def __init__(
        self,
        cfg: SupervisorConfig,
        *,
        port_file: Path | None = None,
        pid_file: Path | None = None,
        lock_file: Path | None = None,
        touch_file: Path | None = None,
        log_file: Path | None = None,
        spawn: Callable[[list[str]], Any] | None = None,
        health: Callable[[str], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cfg = cfg
        # Media and text servers must not share coordination files: the media
        # server carries ``--mmproj`` (heavy) and is only needed while indexing,
        # while the query daemon wants a light text-only server.
        stem = "llama-media" if cfg.media else "llama"
        self.port_file = port_file or (paths.runtime_subdir() / f"{stem}.port")
        self.pid_file = pid_file or (paths.runtime_subdir() / f"{stem}.pid")
        self.lock_file = lock_file or (paths.runtime_subdir() / f"{stem}.lock")
        #: Shared last-activity stamp: the server is shared between daemons, so
        #: *every* user updates this file and the owner only unloads the server
        #: once nobody has touched it for the idle timeout.
        self.touch_file = touch_file or (paths.runtime_subdir() / f"{stem}.touch")
        self.log_file = log_file or (paths.state_dir() / f"{stem}.log")
        self._spawn = spawn or self._default_spawn
        self._health = health or _default_health
        self._clock = clock
        self._wall_clock = wall_clock
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
        # Serialize the reuse/spawn decision across processes so that two daemons
        # starting at the same time cannot each spawn their own server.
        with self._spawn_lock():
            return self._ensure_running_locked()

    @contextlib.contextmanager
    def _spawn_lock(self):
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self.lock_file.parent, 0o700)
        with open(self.lock_file, "w", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                with contextlib.suppress(OSError):
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def _stale_owner(self) -> bool:
        """Whether the server recorded in the pid file is definitely gone.

        When it is, there is no point waiting out ``startup_grace`` for a process
        that no longer exists (that would delay the first query after a crash or
        an idle unload by the whole grace period). Zombies count as gone.
        """
        try:
            pid = int(self.pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return False  # unknown -> use the grace period
        return not _process_alive(pid)

    def _ensure_running_locked(self) -> str:
        port = self._read_port()
        if port is not None:
            url = f"http://127.0.0.1:{port}"
            if self._health(url) or (not self._stale_owner() and self._await_healthy(url)):
                log.debug("reusing llama-server at %s", url)
                self._url = url
                self._owned = False
                self._last_activity = self._clock()
                return url
            log.warning("stale llama-server port file; starting a new server")
            self._remove_files()

        return self._start_server()

    def _start_server(self) -> str:
        """Spawn ``llama-server`` on a free port and return its base URL.

        ``_free_port`` releases its socket before llama-server binds, so in that
        window another process can take the port; the server then exits at once.
        An early exit is therefore retried on a fresh port instead of failing the
        request. A server that stays alive but never becomes healthy is a real
        failure (a bad model or flags) and is reported after one attempt, so a
        broken setup does not pay the health timeout several times over.
        """
        self._stop_previous()
        for attempt in range(1, _SPAWN_ATTEMPTS + 1):
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

            exited = self._proc is not None and self._proc.poll() is not None
            self._remove_files()
            self._terminate()
            self._proc = None
            if not exited:
                break
            if attempt < _SPAWN_ATTEMPTS:
                log.warning(
                    "llama-server exited at startup (attempt %d/%d); trying another port",
                    attempt,
                    _SPAWN_ATTEMPTS,
                )

        raise ServerUnavailable(
            f"llama-server did not become healthy in {self.cfg.health_timeout:.0f}s; "
            f"see {self.log_file}"
        )

    def _await_healthy(self, url: str) -> bool:
        """Give an already-running server a grace period to become ready."""
        deadline = self._clock() + self.cfg.startup_grace
        while self._clock() < deadline:
            if self._health(url):
                return True
            self._sleep(0.5)
        return False

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
        """Record activity, in memory and in the shared stamp file."""
        self._last_activity = self._clock()
        with contextlib.suppress(OSError):
            self.touch_file.parent.mkdir(parents=True, exist_ok=True)
            self.touch_file.write_text(str(self._wall_clock()), encoding="utf-8")

    def _shared_idle_seconds(self) -> float | None:
        """Seconds since the shared activity stamp, or ``None`` without one."""
        try:
            stamp = float(self.touch_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None
        return max(0.0, self._wall_clock() - stamp)

    def stop_if_idle(self) -> bool:
        """Stop an owned server idle for longer than the configured timeout.

        Any process using the shared server refreshes the stamp file, so a
        running indexer keeps the server alive even though the owner's own
        request counter is quiet.
        """
        if not self._owned or self._proc is None or self.cfg.idle_timeout_min <= 0:
            return False
        idle = self._clock() - self._last_activity
        shared = self._shared_idle_seconds()
        if shared is not None:
            idle = min(idle, shared)
        if idle >= self.cfg.idle_timeout_min * 60:
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
        """Stop the process and reap it, so it does not stay a zombie.

        The ``wait`` after ``kill`` matters: a child that ignores SIGTERM and
        needs SIGKILL would otherwise keep its entry in the process table until
        this process exits, and the pid file keeps pointing at it.
        """
        proc = self._proc
        if proc is None:
            return
        with contextlib.suppress(Exception):
            proc.terminate()
        try:
            proc.wait(timeout=_TERMINATE_TIMEOUT_S)
            return
        except Exception as exc:  # noqa: BLE001 - any failure means "kill it"
            log.warning("llama-server did not stop within %.0fs (%s); killing it",
                        _TERMINATE_TIMEOUT_S, exc)
        with contextlib.suppress(Exception):
            proc.kill()
        with contextlib.suppress(Exception):
            proc.wait(timeout=_TERMINATE_TIMEOUT_S)

    def _stop_previous(self) -> None:
        """Stop and reap a child this supervisor no longer tracks, if any.

        A server that exited on its own is never reaped until someone waits for
        it, and one that stayed alive is never terminated when it is replaced;
        both would leave a process behind while a new one starts.
        """
        if self._proc is not None:
            self._terminate()
            self._proc = None
