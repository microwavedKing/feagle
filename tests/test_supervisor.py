"""Tests for the llama-server supervisor."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from feagle.embeddings.supervisor import (
    LlamaSupervisor,
    SupervisorConfig,
    build_llama_command,
)


class FakeProc:
    def __init__(self, pid: int = 4242, exit_code: int | None = None) -> None:
        self.pid = pid
        self.returncode = exit_code
        self._alive = exit_code is None
        self.terminated = False
        self.killed = False

    def poll(self):
        return None if self._alive else self.returncode

    def terminate(self):
        self.terminated = True
        self._alive = False

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True
        self._alive = False


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def make_supervisor(tmp_path, *, health, spawn=None, cfg_kwargs=None, clock=None):
    cfg_kwargs = cfg_kwargs or {}
    cfg = SupervisorConfig(model_path=Path("/models/m.gguf"), **cfg_kwargs)
    clock = clock or Clock()
    return LlamaSupervisor(
        cfg,
        port_file=tmp_path / "llama.port",
        pid_file=tmp_path / "llama.pid",
        lock_file=tmp_path / "llama.lock",
        log_file=tmp_path / "llama.log",
        spawn=spawn or (lambda cmd: FakeProc()),
        health=health,
        clock=clock,
        sleep=clock.sleep,
    ), clock


# -- command building ------------------------------------------------------


def test_command_has_required_flags():
    cfg = SupervisorConfig(model_path=Path("/m.gguf"), context=4096, slots=8)
    cmd = build_llama_command(cfg, 12345)
    assert cmd[0] == "llama-server"
    assert "12345" in cmd
    for flag in ("--embeddings", "--host", "--port", "--no-webui", "--cache-ram", "--alias"):
        assert flag in cmd
    assert "--pooling" not in cmd  # mean pooling is baked in


def test_command_threads_only_when_positive():
    assert "-t" not in build_llama_command(SupervisorConfig(model_path=Path("/m")), 1)
    assert "-t" in build_llama_command(SupervisorConfig(model_path=Path("/m"), threads=4), 1)


def test_command_appends_extra_args():
    cfg = SupervisorConfig(model_path=Path("/m"), extra_args=["--flash-attn"])
    assert "--flash-attn" in build_llama_command(cfg, 1)


# -- lifecycle -------------------------------------------------------------


def test_external_server_url_is_not_managed(tmp_path):
    sup, _ = make_supervisor(
        tmp_path, health=lambda url: True, cfg_kwargs={"server_url": "http://example:8080/"}
    )
    assert sup.ensure_running() == "http://example:8080"
    assert sup.owned is False


def test_reuses_healthy_server_from_port_file(tmp_path):
    (tmp_path / "llama.port").write_text("5050", encoding="utf-8")

    def spawn(cmd):
        raise AssertionError("must not spawn when reusing")

    sup, _ = make_supervisor(tmp_path, health=lambda url: url.endswith(":5050"), spawn=spawn)
    assert sup.ensure_running() == "http://127.0.0.1:5050"
    assert sup.owned is False


def test_waits_for_a_slow_existing_server(tmp_path):
    """Two daemons starting together must not spawn two servers."""
    (tmp_path / "llama.port").write_text("5050", encoding="utf-8")
    state = {"probes": 0}

    def health(url):
        state["probes"] += 1
        return state["probes"] >= 3  # ready on the third probe

    def spawn(cmd):
        raise AssertionError("must not spawn a duplicate server")

    sup, _ = make_supervisor(tmp_path, health=health, spawn=spawn)
    assert sup.ensure_running() == "http://127.0.0.1:5050"
    assert sup.owned is False


def test_concurrent_start_spawns_only_once(tmp_path):
    spawned: list[int] = []
    state: dict[str, int | None] = {"port": None}

    def spawn(cmd):
        port = int(cmd[cmd.index("--port") + 1])
        state["port"] = port
        time.sleep(0.2)  # emulate startup
        (tmp_path / "llama.port").write_text(str(port), encoding="utf-8")
        (tmp_path / "llama.pid").write_text("1", encoding="utf-8")
        spawned.append(port)
        return FakeProc()

    def health(url):
        port = state["port"]
        return port is not None and url.endswith(f":{port}")

    results: list[str] = []

    def worker():
        sup, _ = make_supervisor(tmp_path, health=health, spawn=spawn)
        results.append(sup.ensure_running())

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(set(results)) == 1  # both ended up with the same server
    assert len(spawned) == 1  # and only one was started


def test_spawns_when_no_server(tmp_path):
    sup, _ = make_supervisor(tmp_path, health=lambda url: True)
    url = sup.ensure_running()
    assert url.startswith("http://127.0.0.1:")
    assert sup.owned is True
    assert (tmp_path / "llama.port").read_text() == url.rsplit(":", 1)[1]
    assert (tmp_path / "llama.pid").read_text() == "4242"


def test_stale_port_file_is_replaced(tmp_path):
    (tmp_path / "llama.port").write_text("5050", encoding="utf-8")
    sup, _ = make_supervisor(tmp_path, health=lambda url: ":5050" not in url)
    url = sup.ensure_running()
    assert ":5050" not in url
    assert sup.owned is True
    assert (tmp_path / "llama.port").read_text() != "5050"


def test_unhealthy_spawn_raises_and_cleans_up(tmp_path):
    sup, _ = make_supervisor(
        tmp_path, health=lambda url: False, cfg_kwargs={"health_timeout": 2.0}
    )
    with pytest.raises(Exception, match="did not become healthy"):
        sup.ensure_running()
    assert not (tmp_path / "llama.port").exists()
    assert not (tmp_path / "llama.pid").exists()


def test_early_exit_detected(tmp_path):
    proc = FakeProc(exit_code=1)
    sup, _ = make_supervisor(
        tmp_path,
        health=lambda url: False,
        spawn=lambda cmd: proc,
        cfg_kwargs={"health_timeout": 5.0},
    )
    with pytest.raises(Exception, match="did not become healthy"):
        sup.ensure_running()
    assert proc.terminated is True


def test_idle_stop_terminates_owned_server(tmp_path):
    sup, clock = make_supervisor(
        tmp_path, health=lambda url: True, cfg_kwargs={"idle_timeout_min": 1}
    )
    sup.ensure_running()
    assert sup.stop_if_idle() is False

    clock.t += 61
    assert sup.stop_if_idle() is True
    assert sup.owned is False
    assert not (tmp_path / "llama.port").exists()


def test_touch_resets_idle(tmp_path):
    sup, clock = make_supervisor(
        tmp_path, health=lambda url: True, cfg_kwargs={"idle_timeout_min": 1}
    )
    sup.ensure_running()
    clock.t += 50
    sup.touch()
    clock.t += 50
    assert sup.stop_if_idle() is False  # only 50s since touch


def test_never_stops_reused_server(tmp_path):
    (tmp_path / "llama.port").write_text("5050", encoding="utf-8")
    sup, clock = make_supervisor(
        tmp_path, health=lambda url: True, cfg_kwargs={"idle_timeout_min": 1}
    )
    sup.ensure_running()
    clock.t += 9999
    assert sup.stop_if_idle() is False


def test_stop_removes_files(tmp_path):
    sup, _ = make_supervisor(tmp_path, health=lambda url: True)
    sup.ensure_running()
    sup.stop()
    assert not (tmp_path / "llama.port").exists()
    assert sup.owned is False
