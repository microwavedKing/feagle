"""Tests for image preparation (downscaling) used by media embeddings."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from feagle.indexer import media


def _which(names):
    return lambda name: f"/usr/bin/{name}" if name in names else None


def test_availability(monkeypatch):
    monkeypatch.setattr(media.shutil, "which", _which({"ffmpeg", "ffprobe"}))
    assert media.ffmpeg_available() is True
    assert media.ffprobe_available() is True
    monkeypatch.setattr(media.shutil, "which", _which(set()))
    assert media.ffmpeg_available() is False
    assert media.ffprobe_available() is False


def test_image_dimensions_parses_ffprobe_output(tmp_path, monkeypatch):
    monkeypatch.setattr(media.shutil, "which", _which({"ffprobe"}))
    monkeypatch.setattr(
        media.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, b"1700x2200\n", b""),
    )
    assert media.image_dimensions(tmp_path / "x.png") == (1700, 2200)


def test_image_dimensions_reports_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(media.shutil, "which", _which({"ffprobe"}))
    monkeypatch.setattr(
        media.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, b"", b"boom"),
    )
    with pytest.raises(media.MediaError, match="ffprobe exited"):
        media.image_dimensions(tmp_path / "x.png")


def _fake_run(calls, dimensions):
    def run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0].endswith("ffprobe"):
            return subprocess.CompletedProcess(cmd, 0, f"{dimensions}\n".encode(), b"")
        Path(cmd[-1]).write_bytes(b"PNG")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    return run


def test_downscale_caps_the_longest_side(tmp_path, monkeypatch):
    monkeypatch.setattr(media.shutil, "which", _which({"ffmpeg", "ffprobe"}))
    calls = []
    monkeypatch.setattr(media.subprocess, "run", _fake_run(calls, "2200x1700"))
    src = tmp_path / "in.png"
    src.write_bytes(b"x")
    dest = tmp_path / "out.png"

    assert media.downscale_image(src, dest, max_dim=1024) == dest
    ffmpeg = next(c for c in calls if c[0].endswith("ffmpeg"))
    assert "scale=1024:-1" in ffmpeg  # landscape -> cap the width


def test_downscale_caps_the_height_for_portrait(tmp_path, monkeypatch):
    monkeypatch.setattr(media.shutil, "which", _which({"ffmpeg", "ffprobe"}))
    calls = []
    monkeypatch.setattr(media.subprocess, "run", _fake_run(calls, "1700x2200"))
    src = tmp_path / "in.png"
    src.write_bytes(b"x")
    dest = tmp_path / "out.png"

    assert media.downscale_image(src, dest, max_dim=1024) == dest
    ffmpeg = next(c for c in calls if c[0].endswith("ffmpeg"))
    assert "scale=-1:1024" in ffmpeg


def test_small_image_is_not_scaled(tmp_path, monkeypatch):
    monkeypatch.setattr(media.shutil, "which", _which({"ffmpeg", "ffprobe"}))
    calls = []
    monkeypatch.setattr(media.subprocess, "run", _fake_run(calls, "640x480"))
    src = tmp_path / "in.png"
    src.write_bytes(b"x")

    assert media.downscale_image(src, tmp_path / "out.png", max_dim=1024) == src
    assert not any(c[0].endswith("ffmpeg") for c in calls)


def test_downscaling_can_be_disabled(tmp_path):
    # max_dim <= 0 returns the source without even probing it
    src = tmp_path / "in.png"
    src.write_bytes(b"x")
    assert media.downscale_image(src, tmp_path / "out.png", max_dim=0) == src


def test_downscale_without_ffmpeg_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(media.shutil, "which", _which({"ffprobe"}))
    calls = []
    monkeypatch.setattr(media.subprocess, "run", _fake_run(calls, "1700x2200"))
    src = tmp_path / "in.png"
    src.write_bytes(b"x")
    with pytest.raises(media.MediaError, match="ffmpeg not found"):
        media.downscale_image(src, tmp_path / "out.png", max_dim=1024)


# -- video frames ----------------------------------------------------------


def test_extract_frames_builds_command_and_times(tmp_path, monkeypatch):
    monkeypatch.setattr(media.shutil, "which", _which({"ffmpeg"}))
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        for i in range(3):
            Path(cmd[-1].replace("%05d", f"{i + 1:05d}")).write_bytes(b"PNG")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(media.subprocess, "run", run)
    frames = media.extract_frames(
        tmp_path / "v.mp4", tmp_path / "frames", interval=10.0, max_frames=3, max_dim=512
    )
    assert [time_ms for time_ms, _ in frames] == [0, 10000, 20000]
    command = calls[0]
    assert any("fps=1/10.0" in token for token in command)
    assert any("scale=512:-1" in token for token in command)
    assert command[command.index("-frames:v") + 1] == "3"


def test_extract_frames_without_ffmpeg_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(media.shutil, "which", _which(set()))
    with pytest.raises(media.MediaError, match="ffmpeg not found"):
        media.extract_frames(tmp_path / "v.mp4", tmp_path / "frames")
