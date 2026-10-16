"""Configuration loading, defaults and validation.

Config is TOML at ``$XDG_CONFIG_HOME/feagle/config.toml``. Defaults are merged
with the user file; unknown keys produce a warning; invalid values raise
:class:`ConfigError` with the offending key path and file location.
"""

from __future__ import annotations

import copy
import tomllib
from pathlib import Path
from typing import Any

from . import paths
from .log import get_logger

log = get_logger("config")

SEARCH_MODES = ("auto", "hybrid", "fts")
STEMMING_MODES = ("auto", "none")
LAUNCHERS = ("auto", "fuzzel", "wofi", "rofi", "tofi", "walker", "stdout")
MRL_DIMS = (128, 256, 512, 768)

DEFAULTS: dict[str, Any] = {
    "index": {
        "roots": [],
        "include_ext": [
            ".txt", ".md", ".rst", ".tex", ".pdf", ".py", ".c", ".h", ".cpp", ".hpp",
            ".rs", ".go", ".js", ".ts", ".java", ".sh", ".lua", ".toml", ".yaml",
            ".yml", ".json", ".cfg", ".ini", ".csv", ".org",
        ],
        "exclude_dirs": [
            ".git", "node_modules", ".cache", "__pycache__", ".venv", "venv",
            "dist", "build", "target", ".mypy_cache", ".pytest_cache",
        ],
        "use_gitignore": True,
        "max_file_size_mb": 10,
        "max_pdf_file_size_mb": 50,
        "max_chunks_per_file": 1000,
        "rescan_interval_min": 360,
        "pause_on_battery": True,
        "pause_on_loadavg": 4.0,
        "embed_batch_size": 16,
        "follow_symlinks": False,
        "ocr": False,
        "ocr_images": False,
        "ocr_langs": "rus+eng",
        "ocr_dpi": 300,
        "ocr_min_chars": 20,
        "ocr_max_pages": 200,
    },
    "embeddings": {
        "model": "embeddinggemma-2-q4",
        "mrl_dim": 768,
        "server_url": "",
        "idle_timeout_min": 10,
        "threads": 0,
        "extra_args": [],
    },
    "media": {
        "images": False,
        "mmproj": "embeddinggemma-2-mmproj-q8",
        "image_max_dim": 1024,
        "images_per_run": 200,
        "video": False,
        "video_frame_interval": 10,
        "video_max_frames": 60,
        "video_ocr": False,
        "audio": False,
        "whisper_binary": "whisper-cli",
        "whisper_model": "ggml-base.bin",
        "audio_language": "auto",
    },
    "search": {
        "limit": 20,
        "mode": "auto",
        "candidates": 50,
        "rrf_k": 60,
        "stemming": "auto",
        # Drop vector neighbours whose L2 distance (on normalized vectors) is
        # above this; unrelated results otherwise always fill the list. 0 disables.
        "max_distance": 0.8,
        # At most this many chunks of the same file in the results (0 = no cap).
        "max_per_file": 3,
    },
    "launcher": {
        "program": "auto",
    },
    "openers": {
        "default": "xdg-open {path}",
        "pdf": "zathura --page {page} {path}",
    },
}


class ConfigError(Exception):
    """Raised when the configuration file contains an invalid value."""


def default_config() -> dict[str, Any]:
    """Return a deep copy of the built-in defaults."""
    return copy.deepcopy(DEFAULTS)


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` into ``base`` (returns ``base``)."""
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def _warn_unknown(
    user: dict[str, Any],
    defaults: dict[str, Any],
    prefix: str = "",
    free_tables: frozenset[str] = frozenset({"openers"}),
) -> None:
    """Warn about keys that are not part of the schema.

    ``openers`` is a free-form table: its keys are file extensions, so unknown
    entries there are expected and must not be reported.
    """
    for key, value in user.items():
        path = f"{prefix}{key}"
        if key not in defaults:
            log.warning("unknown config key ignored: %s", path)
        elif isinstance(value, dict) and isinstance(defaults[key], dict):
            if key in free_tables:
                continue
            _warn_unknown(value, defaults[key], prefix=f"{path}.", free_tables=free_tables)


def _check(condition: bool, key: str, message: str, source: Path) -> None:
    if not condition:
        raise ConfigError(f"{source}: [{key}] {message}")


def _check_type(cfg: dict[str, Any], key: str, types: tuple[type, ...], source: Path) -> None:
    value = cfg[key]
    # bool is a subclass of int; keep them distinct.
    if isinstance(value, bool) and bool not in types:
        raise ConfigError(f"{source}: [{key}] expected {'/'.join(t.__name__ for t in types)}")
    if not isinstance(value, types) or (isinstance(value, bool) and bool not in types):
        names = "/".join(t.__name__ for t in types)
        raise ConfigError(f"{source}: [{key}] expected {names}, got {type(value).__name__}")


def validate(cfg: dict[str, Any], source: Path | None = None) -> None:
    """Validate a merged config, raising :class:`ConfigError` on problems."""
    source = source or paths.config_file()

    index = cfg["index"]
    for key in ("roots", "include_ext", "exclude_dirs"):
        _check_type(index, key, (list,), source)
        _check(
            all(isinstance(item, str) for item in index[key]),
            f"index.{key}",
            "all entries must be strings",
            source,
        )
    _check_type(index, "use_gitignore", (bool,), source)
    _check_type(index, "follow_symlinks", (bool,), source)
    _check_type(index, "pause_on_battery", (bool,), source)
    for key in ("max_file_size_mb", "max_pdf_file_size_mb", "max_chunks_per_file",
                "embed_batch_size"):
        _check_type(index, key, (int,), source)
        _check(index[key] > 0, f"index.{key}", "must be > 0", source)
    _check_type(index, "rescan_interval_min", (int,), source)
    _check(index["rescan_interval_min"] >= 0, "index.rescan_interval_min", "must be >= 0", source)
    _check_type(index, "pause_on_loadavg", (int, float), source)
    _check(index["pause_on_loadavg"] >= 0, "index.pause_on_loadavg", "must be >= 0", source)
    _check_type(index, "ocr", (bool,), source)
    _check_type(index, "ocr_images", (bool,), source)
    _check_type(index, "ocr_langs", (str,), source)
    _check(bool(index["ocr_langs"].strip()), "index.ocr_langs", "must not be empty", source)
    for key in ("ocr_dpi", "ocr_max_pages"):
        _check_type(index, key, (int,), source)
        _check(index[key] > 0, f"index.{key}", "must be > 0", source)
    _check_type(index, "ocr_min_chars", (int,), source)
    _check(index["ocr_min_chars"] >= 0, "index.ocr_min_chars", "must be >= 0", source)

    emb = cfg["embeddings"]
    _check_type(emb, "model", (str,), source)
    _check(bool(emb["model"]), "embeddings.model", "must not be empty", source)
    _check_type(emb, "mrl_dim", (int,), source)
    _check(emb["mrl_dim"] in MRL_DIMS, "embeddings.mrl_dim",
           f"must be one of {MRL_DIMS}", source)
    _check_type(emb, "server_url", (str,), source)
    _check_type(emb, "idle_timeout_min", (int, float), source)
    _check(emb["idle_timeout_min"] >= 0, "embeddings.idle_timeout_min", "must be >= 0", source)
    _check_type(emb, "threads", (int,), source)
    _check(emb["threads"] >= 0, "embeddings.threads", "must be >= 0", source)
    _check_type(emb, "extra_args", (list,), source)
    _check(all(isinstance(a, str) for a in emb["extra_args"]), "embeddings.extra_args",
           "all entries must be strings", source)

    media = cfg["media"]
    _check_type(media, "images", (bool,), source)
    _check_type(media, "mmproj", (str,), source)
    _check(bool(media["mmproj"].strip()), "media.mmproj", "must not be empty", source)
    _check_type(media, "image_max_dim", (int,), source)
    _check(media["image_max_dim"] >= 0, "media.image_max_dim", "must be >= 0", source)
    _check_type(media, "images_per_run", (int,), source)
    _check(media["images_per_run"] >= 0, "media.images_per_run", "must be >= 0", source)
    _check_type(media, "video", (bool,), source)
    _check_type(media, "video_frame_interval", (int, float), source)
    _check(media["video_frame_interval"] > 0, "media.video_frame_interval", "must be > 0", source)
    _check_type(media, "video_max_frames", (int,), source)
    _check(media["video_max_frames"] > 0, "media.video_max_frames", "must be > 0", source)
    _check_type(media, "video_ocr", (bool,), source)
    _check_type(media, "audio", (bool,), source)
    _check_type(media, "whisper_binary", (str,), source)
    _check(
        bool(media["whisper_binary"].strip()),
        "media.whisper_binary", "must not be empty", source,
    )
    _check_type(media, "whisper_model", (str,), source)
    _check(
        bool(media["whisper_model"].strip()),
        "media.whisper_model", "must not be empty", source,
    )
    _check_type(media, "audio_language", (str,), source)
    _check(
        bool(media["audio_language"].strip()),
        "media.audio_language", "must not be empty", source,
    )

    search = cfg["search"]
    _check_type(search, "limit", (int,), source)
    _check(search["limit"] > 0, "search.limit", "must be > 0", source)
    _check_type(search, "mode", (str,), source)
    _check(search["mode"] in SEARCH_MODES, "search.mode",
           f"must be one of {SEARCH_MODES}", source)
    _check_type(search, "candidates", (int,), source)
    _check(search["candidates"] > 0, "search.candidates", "must be > 0", source)
    _check_type(search, "rrf_k", (int,), source)
    _check(search["rrf_k"] > 0, "search.rrf_k", "must be > 0", source)
    _check_type(search, "stemming", (str,), source)
    _check(search["stemming"] in STEMMING_MODES, "search.stemming",
           f"must be one of {STEMMING_MODES}", source)
    _check_type(search, "max_distance", (int, float), source)
    _check(search["max_distance"] >= 0, "search.max_distance", "must be >= 0", source)
    _check_type(search, "max_per_file", (int,), source)
    _check(search["max_per_file"] >= 0, "search.max_per_file", "must be >= 0", source)

    launcher = cfg["launcher"]
    _check_type(launcher, "program", (str,), source)
    _check(launcher["program"] in LAUNCHERS, "launcher.program",
           f"must be one of {LAUNCHERS}", source)

    openers = cfg["openers"]
    _check(isinstance(openers, dict), "openers", "must be a table", source)
    _check("default" in openers, "openers.default", "is required", source)
    _check(
        all(isinstance(k, str) and isinstance(v, str) for k, v in openers.items()),
        "openers", "every opener must be a string template",
        source,
    )
    _check(bool(openers["default"].strip()), "openers.default", "must not be empty", source)


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Load, merge with defaults and validate the configuration.

    A missing file yields the built-in defaults.
    """
    path = path or paths.config_file()
    user: dict[str, Any] = {}
    if path.exists():
        with open(path, "rb") as fh:
            try:
                user = tomllib.load(fh)
            except tomllib.TOMLDecodeError as exc:
                raise ConfigError(f"{path}: invalid TOML: {exc}") from exc

    cfg = _merge(default_config(), user)
    _warn_unknown(user, DEFAULTS)
    validate(cfg, source=path)
    return cfg


def render_defaults() -> str:
    """Render the default configuration as commented TOML text."""
    return _DEFAULTS_TOML


_DEFAULTS_TOML = """\
# feagle configuration.
# Location: $XDG_CONFIG_HOME/feagle/config.toml (usually ~/.config/feagle/config.toml)
# Every value below is optional; it shows the built-in default.

[index]
# Directories to index (absolute paths). Empty means nothing is indexed.
roots = []
# File extensions treated as text/code (allowlist).
include_ext = [".txt",".md",".rst",".tex",".pdf",".py",".c",".h",".cpp",".hpp",
               ".rs",".go",".js",".ts",".java",".sh",".lua",".toml",".yaml",
               ".yml",".json",".cfg",".ini",".csv",".org"]
# Directory names never descended into.
exclude_dirs = [".git","node_modules",".cache","__pycache__",".venv","venv",
                "dist","build","target",".mypy_cache",".pytest_cache"]
use_gitignore = true
max_file_size_mb = 10
max_pdf_file_size_mb = 50
max_chunks_per_file = 1000
# Full stat-based rescan interval, in minutes (0 disables periodic rescan).
rescan_interval_min = 360
pause_on_battery = true
# Pause indexing when the 1-minute load average exceeds this (0 disables).
pause_on_loadavg = 4.0
embed_batch_size = 16
follow_symlinks = false
# OCR for scanned PDFs (needs tesseract + pdftoppm and tesseract-data-*). Off by default.
ocr = false
# OCR image files (png/jpg/jpeg/webp/tiff) directly with tesseract. Off by default.
ocr_images = false
ocr_langs = "rus+eng"
ocr_dpi = 300
# A page is OCR'd only when its text layer has fewer than this many characters.
ocr_min_chars = 20
# Safety limit on how many pages per PDF are OCR'd (OCR is slow).
ocr_max_pages = 200

[embeddings]
# Preset name from embeddings/presets.py, or an absolute path to a GGUF file.
# q4 is the light default (about half the size of q8, ~30% faster indexing,
# same measured retrieval quality); q8 is the official-repo alternative.
model = "embeddinggemma-2-q4"
# Matryoshka truncation, one of 128, 256, 512, 768.
mrl_dim = 768
# External llama-server base URL. Empty means feagle manages the server itself.
server_url = ""
idle_timeout_min = 10
threads = 0
extra_args = []

[media]
# Index images (png/jpg/jpeg/webp/tiff) as embeddings so text queries can find
# them ("search by picture"). Needs the multimodal projector below, ffmpeg for
# downscaling, and is CPU-heavy (tens of seconds per large image). Off by default.
images = false
# mmproj preset name from embeddings/presets.py, or an absolute path to a GGUF.
mmproj = "embeddinggemma-2-mmproj-q8"
# Downscale an image so its longest side is at most this many pixels before
# embedding (smaller = faster and less RAM; 0 disables downscaling).
image_max_dim = 1024
# Safety cap: at most this many images are embedded per indexing pass
# (0 = unlimited). The rest are picked up by later passes / backfill.
images_per_run = 200
# Index video files by embedding extracted frames (needs ffmpeg + mmproj above).
video = false
# One frame every this many seconds.
video_frame_interval = 10
# At most this many frames per video (safety cap; embedding is slow).
video_max_frames = 60
# Also OCR each frame (tesseract) so text shown in the video is searchable.
video_ocr = false
# Transcribe audio files with whisper.cpp so spoken words become searchable.
audio = false
# whisper-cli binary (name on PATH or an absolute path).
whisper_binary = "whisper-cli"
# GGML whisper model: a filename under the models dir, or an absolute path.
whisper_model = "ggml-base.bin"
# Spoken language: "auto" (detect) or a code like "en" / "ru".
audio_language = "auto"

[search]
limit = 20
# auto | hybrid | fts
mode = "auto"
candidates = 50
rrf_k = 60
# Russian stemming for the full-text index: auto | none
stemming = "auto"
# Ignore vector neighbours farther than this (L2 on normalized vectors; 0 = off).
max_distance = 0.8
# Maximum chunks of one file in the results (0 = unlimited).
max_per_file = 3

[launcher]
# auto | fuzzel | wofi | rofi | tofi | walker | stdout
program = "auto"

[openers]
# Placeholders: {path} {line} {page}. "default" is required.
default = "xdg-open {path}"
pdf = "zathura --page {page} {path}"
"""
