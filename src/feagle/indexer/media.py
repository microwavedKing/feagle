"""Image preparation for multimodal embeddings.

Large images are slow and memory-hungry to encode (the vision tower works on
pixels), so before an image is embedded it is downscaled so that its longest
side is at most ``image_max_dim``. ``ffprobe`` reads the size and ``ffmpeg``
does the scaling; both come with an ``ffmpeg`` install. When they are missing
the image is skipped with a clear message instead of failing the run.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..log import get_logger

log = get_logger("indexer.media")

FFMPEG_TIMEOUT = 60.0
#: Extracting frames from a long video can take much longer than one image.
VIDEO_TIMEOUT = 900.0

#: Video extensions recognized (frames are extracted with ``ffmpeg``).
VIDEO_SUFFIXES: tuple[str, ...] = (
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".mpg", ".mpeg",
)


class MediaError(Exception):
    """An image could not be measured or scaled."""


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def ffprobe_available() -> bool:
    return shutil.which("ffprobe") is not None


def image_dimensions(
    path: Path,
    *,
    ffprobe: str | None = None,
    timeout: float = FFMPEG_TIMEOUT,
) -> tuple[int, int]:
    """Return ``(width, height)`` of the first video stream. Raises :class:`MediaError`."""
    binary = ffprobe or shutil.which("ffprobe")
    if binary is None:
        raise MediaError("ffprobe not found")
    try:
        proc = subprocess.run(
            [
                binary, "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x",
                str(path),
            ],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MediaError(f"ffprobe failed for {path}: {exc}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise MediaError(f"ffprobe exited {proc.returncode}: {detail[:200]}")
    first = proc.stdout.decode("utf-8", errors="replace").strip().splitlines()
    if not first:
        raise MediaError(f"ffprobe returned no size for {path}")
    try:
        width, height = (int(part) for part in first[0].split("x"))
    except ValueError as exc:
        raise MediaError(f"cannot parse image size {first[0]!r}") from exc
    return width, height


def downscale_image(
    src: Path,
    dest: Path,
    *,
    max_dim: int,
    ffmpeg: str | None = None,
    ffprobe: str | None = None,
    timeout: float = FFMPEG_TIMEOUT,
) -> Path:
    """Return a path to embed: ``src`` if small enough, else a scaled ``dest``.

    Scaling preserves the aspect ratio and caps the longest side at ``max_dim``.
    ``max_dim <= 0`` disables downscaling entirely.
    """
    if max_dim <= 0:
        return src
    width, height = image_dimensions(src, ffprobe=ffprobe, timeout=timeout)
    if max(width, height) <= max_dim:
        return src

    binary = ffmpeg or shutil.which("ffmpeg")
    if binary is None:
        raise MediaError("ffmpeg not found")
    scale = f"scale={max_dim}:-1" if width >= height else f"scale=-1:{max_dim}"
    try:
        proc = subprocess.run(
            [binary, "-y", "-loglevel", "error", "-i", str(src), "-vf", scale, str(dest)],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MediaError(f"ffmpeg failed for {src}: {exc}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise MediaError(f"ffmpeg exited {proc.returncode}: {detail[:200]}")
    if not dest.exists():
        raise MediaError(f"ffmpeg produced no file for {src}")
    return dest


def extract_frames(
    video: Path,
    dest_dir: Path,
    *,
    interval: float = 10.0,
    max_frames: int = 60,
    max_dim: int = 0,
    ffmpeg: str | None = None,
    timeout: float = VIDEO_TIMEOUT,
) -> list[tuple[int, Path]]:
    """Extract up to ``max_frames`` PNG frames, one every ``interval`` seconds.

    Returns ``(time_ms, path)`` pairs in order. ``max_dim > 0`` scales the longest
    side of each frame while extracting (so a 1080p video does not fill the disk).
    """
    binary = ffmpeg or shutil.which("ffmpeg")
    if binary is None:
        raise MediaError("ffmpeg not found")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    seconds = interval if interval > 0 else 1.0
    filters = f"fps=1/{seconds}"
    if max_dim > 0:
        filters += f",scale={max_dim}:-1"
    pattern = dest_dir / "frame_%05d.png"
    command = [
        binary, "-y", "-loglevel", "error", "-i", str(video),
        "-vf", filters, "-frames:v", str(max(1, max_frames)), str(pattern),
    ]
    try:
        proc = subprocess.run(command, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MediaError(f"ffmpeg failed for {video}: {exc}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise MediaError(f"ffmpeg exited {proc.returncode}: {detail[:200]}")

    frames = sorted(dest_dir.glob("frame_*.png"))
    return [(round(index * seconds * 1000), path) for index, path in enumerate(frames)]
