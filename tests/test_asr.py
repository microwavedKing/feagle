"""Tests for the whisper.cpp ASR integration (no real whisper required)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from feagle.indexer import asr


def _which(names):
    return lambda name: f"/usr/bin/{name}" if name in names else None


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return tmp_path


def test_availability(monkeypatch):
    monkeypatch.setattr(asr.shutil, "which", _which({"whisper-cli", "ffmpeg"}))
    assert asr.whisper_available() is True
    assert asr.asr_available() is True
    monkeypatch.setattr(asr.shutil, "which", _which({"ffmpeg"}))
    assert asr.whisper_available() is False
    assert asr.asr_available() is False
    monkeypatch.setattr(asr.shutil, "which", _which({"whisper-cli"}))
    assert asr.asr_available() is False  # no ffmpeg


def test_resolve_model_bare_name(env):
    from feagle import paths

    assert asr.resolve_model("ggml-base.bin") == paths.models_dir() / "ggml-base.bin"


def test_resolve_model_absolute_path(tmp_path):
    target = tmp_path / "ggml.bin"
    assert asr.resolve_model(str(target)) == target


def test_transcribe_parses_segments(tmp_path, monkeypatch):
    monkeypatch.setattr(asr.shutil, "which", _which({"whisper-cli", "ffmpeg"}))
    model = tmp_path / "ggml-base.bin"
    model.write_bytes(b"m")
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"a")

    def run(cmd, **kwargs):
        if cmd[0].endswith("ffmpeg"):
            Path(cmd[-1]).write_bytes(b"WAV")
            return subprocess.CompletedProcess(cmd, 0, b"", b"")
        out = Path(cmd[cmd.index("-of") + 1]).with_suffix(".json")
        out.write_text(
            json.dumps({
                "transcription": [
                    {"offsets": {"from": 0, "to": 2500}, "text": " Hello there. "},
                    {"offsets": {"from": 2500, "to": 5000}, "text": "   "},  # blank
                    {"offsets": {"from": 5000, "to": 8000}, "text": "General Kenobi."},
                ]
            }),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(asr.subprocess, "run", run)
    segments = asr.transcribe(audio, model=model, whisper="whisper-cli", language="en")

    assert [(s.start_ms, s.text) for s in segments] == [
        (0, "Hello there."),
        (5000, "General Kenobi."),
    ]


def test_transcribe_without_binary_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(asr.shutil, "which", _which(set()))
    with pytest.raises(asr.AsrError, match="whisper-cli not found"):
        asr.transcribe(tmp_path / "a.wav", model=tmp_path / "m.bin")


def test_transcribe_missing_model_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(asr.shutil, "which", _which({"whisper-cli", "ffmpeg"}))
    with pytest.raises(asr.AsrError, match="model not found"):
        asr.transcribe(
            tmp_path / "a.wav", model=tmp_path / "missing.bin", whisper="whisper-cli"
        )


def test_transcribe_reports_ffmpeg_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(asr.shutil, "which", _which({"whisper-cli", "ffmpeg"}))
    model = tmp_path / "m.bin"
    model.write_bytes(b"m")
    monkeypatch.setattr(
        asr.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, b"", b"boom"),
    )
    with pytest.raises(asr.AsrError, match="ffmpeg exited"):
        asr.transcribe(tmp_path / "a.mp3", model=model, whisper="whisper-cli")
