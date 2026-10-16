"""Speech-to-text for audio files via whisper.cpp.

Audio is converted to 16 kHz mono WAV with ``ffmpeg`` and transcribed with
``whisper-cli`` (from whisper.cpp) using its JSON output, so every segment keeps
its start time. The transcript feeds the normal text index (FTS + text
embeddings), and the timestamps let ``feagle open`` jump to the moment.

Both ``whisper-cli`` and ``ffmpeg`` are optional system dependencies: without
them audio files are skipped with a clear message.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .. import paths
from ..log import get_logger

log = get_logger("indexer.asr")

DEFAULT_BINARY = "whisper-cli"
FFMPEG_TIMEOUT = 120.0
ASR_TIMEOUT = 1800.0

#: Audio extensions recognized (all are converted through ffmpeg first).
AUDIO_SUFFIXES: tuple[str, ...] = (
    ".mp3", ".wav", ".flac", ".ogg", ".oga", ".opus",
    ".m4a", ".aac", ".wma", ".aiff", ".aif",
)


class AsrError(Exception):
    """Audio could not be converted or transcribed."""


@dataclass(frozen=True)
class Segment:
    """A transcribed span with its position in the audio."""

    start_ms: int
    end_ms: int
    text: str


def whisper_available(binary: str | None = None) -> bool:
    return shutil.which(binary or DEFAULT_BINARY) is not None


def asr_available(binary: str | None = None) -> bool:
    """Whether audio can be transcribed: ``whisper-cli`` plus ``ffmpeg``."""
    return whisper_available(binary) and shutil.which("ffmpeg") is not None


def resolve_model(name: str) -> Path:
    """Resolve ``[media].whisper_model``: a bare filename lives in the models dir."""
    path = Path(name).expanduser()
    if path.is_absolute() or path.parent != Path("."):
        return path
    return paths.models_dir() / name


def _to_wav(
    src: Path, dest: Path, *, ffmpeg: str | None = None, timeout: float = FFMPEG_TIMEOUT
) -> None:
    binary = ffmpeg or shutil.which("ffmpeg")
    if binary is None:
        raise AsrError("ffmpeg not found")
    try:
        proc = subprocess.run(
            [
                binary, "-y", "-loglevel", "error", "-i", str(src),
                "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(dest),
            ],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AsrError(f"ffmpeg failed for {src}: {exc}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise AsrError(f"ffmpeg exited {proc.returncode}: {detail[:200]}")


def transcribe(
    path: Path,
    *,
    model: Path,
    whisper: str | None = None,
    language: str = "auto",
    timeout: float = ASR_TIMEOUT,
    ffmpeg: str | None = None,
) -> list[Segment]:
    """Transcribe ``path`` and return segments with timestamps. Raises :class:`AsrError`."""
    whisper_bin = whisper or shutil.which(DEFAULT_BINARY)
    if whisper_bin is None:
        raise AsrError("whisper-cli not found")
    model_path = Path(model).expanduser()
    if not model_path.exists():
        raise AsrError(f"whisper model not found: {model_path}")

    with tempfile.TemporaryDirectory(prefix="feagle-asr-") as tmp:
        wav = Path(tmp) / "audio.wav"
        _to_wav(path, wav, ffmpeg=ffmpeg)
        out_base = Path(tmp) / "out"
        command = [
            whisper_bin, "-m", str(model_path), "-f", str(wav),
            "-oj", "-of", str(out_base),
        ]
        if language and language != "auto":
            command += ["-l", language]
        try:
            proc = subprocess.run(command, capture_output=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AsrError(f"whisper-cli failed for {path}: {exc}") from exc
        if proc.returncode != 0:
            detail = proc.stderr.decode("utf-8", errors="replace").strip()
            raise AsrError(f"whisper-cli exited {proc.returncode}: {detail[:200]}")

        json_path = out_base.with_suffix(".json")
        if not json_path.exists():
            raise AsrError(f"whisper-cli produced no JSON for {path}")
        try:
            entries = json.loads(json_path.read_text(encoding="utf-8"))["transcription"]
        except (ValueError, KeyError, TypeError) as exc:
            raise AsrError(f"malformed whisper JSON for {path}: {exc}") from exc

    segments: list[Segment] = []
    for entry in entries:
        text = str(entry.get("text", "")).strip()
        if not text:
            continue
        offsets = entry.get("offsets") or {}
        start = int(offsets.get("from", 0))
        end = int(offsets.get("to", start))
        segments.append(Segment(start_ms=start, end_ms=end, text=text))
    return segments
