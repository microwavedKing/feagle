# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-10-15

First release: local, offline semantic + full-text file search for Arch Linux
on Wayland.

### Added

- XDG path resolution (`paths.py`) and stderr logging (`log.py`) for journald.
- TOML configuration with defaults, merge, validation and a documented template
  (`config.py`).
- SQLite storage: schema, WAL pragmas, migrations and read-only connections
  (`db.py`).
- Indexer: minimal `.gitignore` matcher, filesystem scanner with filters and
  limits, text/code extraction with binary detection, a character-based chunker
  with page/line positions, and an incremental indexing pipeline with hashing,
  pruning and rebuild support.
- Full-text search over FTS5 with query sanitization (BM25 ranking).
- Embeddings: model presets with pinned SHA256, EmbeddingGemma 2 task prompts,
  an HTTP client for `/v1/embeddings` (batching, dimension discovery, MRL
  truncation with re-normalization), streamed model download with verification,
  and a `llama-server` supervisor (reuse via port file, lazy start, idle stop).
- Vector storage: `sqlite-vec` loading and a dimension-checked `chunks_vec`
  table, plus KNN search (`search/vec.py`).
- Hybrid search: Reciprocal Rank Fusion of BM25 and vector rankings, with
  AND-only FTS to avoid single-word OR noise.
- `queryd`: NDJSON protocol over a Unix socket (0600) with a client,
  per-connection read-only database connections and idle-stop of the server.
- CLI: `index` (`--root`, `--full`, `--rebuild`, `--no-embed`, `--backfill`),
  `search` (`--mode auto|hybrid|fts`, `--limit`, `--json`, `--pick`,
  `--with-apps`), `status`, `open`, `doctor`, `setup`, `queryd`, `indexerd`,
  `bench`.
- PDF indexing: `pdftotext` extraction with per-page text (`\f` pagination),
  timeouts and a clear message when poppler is missing.
- `feagle open <path> [+N]` with configurable opener templates (`openers.py`)
  and a `--dry-run` mode.
- Indexing daemon (`feagle indexerd`): `watchfiles` loop, periodic rescan,
  per-root rescan on change, and battery/load pauses (`power.py`).
- systemd user units for the indexer and query daemon with resource limits, and
  an installer (`units.py`).
- `feagle doctor`: environment checks with concrete fix hints.
- `feagle setup`: interactive first-run configuration (roots, launcher, model,
  download, units, first index) with `--yes` and flags for non-interactive use.
- Russian stemming: a second FTS column holds Snowball stems of Cyrillic words;
  exact and stemmed rankings are fused with RRF (schema v2 with automatic
  migration).
- OCR for scanned PDFs: pages without a text layer are rendered with `pdftoppm`
  and recognized with `tesseract`; configurable languages (default `rus+eng`),
  DPI, trigger threshold and page limit.
- Launcher adapters (`launchers.py`) for fuzzel, wofi, rofi, tofi and walker
  with auto-detection and a `stdout` fallback; `feagle pick` lists desktop
  applications first and indexed files after them, and `search --pick
  --with-apps` mixes applications into search results.
- Search quality controls: `[search].max_distance` drops unrelated vector
  neighbours and `[search].max_per_file` keeps one document from filling the
  result list.
- Legacy text encodings: non-UTF-8 files are decoded with a cp1251 (Cyrillic) /
  cp1252 / latin-1 fallback instead of becoming mojibake.
- Benchmarks: `feagle bench` and the `bench/` scripts on a deterministic
  synthetic corpus (indexing throughput, query latency, memory, database size,
  MRR@10/Recall@20); measured numbers are in the README.
- `embeddinggemma-2-q4` model preset (unsloth mirror, SHA256-pinned) — the light
  default.
- Packaging: `PKGBUILD` + `feagle.install` for the AUR, and a GitHub Actions
  workflow (ruff + pytest on Python 3.12/3.13/3.14).
- Hotkey examples for Hyprland (hyprlang and the new Lua config), Sway, niri,
  River and mango (TOML since 0.18).

### Changed

- The default embedding model is `embeddinggemma-2-q4`: measured 27 % faster
  indexing and ~65 MiB less memory than `q8` with identical retrieval quality.
- `feagle search` uses a running `queryd` when available and otherwise searches
  in-process (degrading to full-text without embeddings).

### Performance

- Stemming results are cached, cutting the indexer's own CPU by ~3.9× on
  Cyrillic-heavy text (profiled before/after; the model still dominates
  wall-clock).

### Fixed

- systemd user units are written with an absolute `feagle` path, so venv and
  pipx installs can actually start them.
- The CLI configures logging: daemons log at INFO to journald, interactive
  commands stay quiet.
- `llama-server` runs with enough per-slot context for the largest chunk, and a
  chunk the server rejects no longer costs the whole file its vectors.
- Two daemons starting at the same time serialize on a lock file and share one
  `llama-server` instead of each spawning their own.
- The units no longer let systemd manage the shared runtime directory: a restart
  of one unit used to delete the other's socket.
- After the embeddings server was unloaded or crashed, the first query stayed
  full-text-only until the retry cooldown expired; it now restarts the server on
  the same request (~1.6 s). A port file whose process is gone (including a
  zombie) is detected immediately instead of waiting out the 15 s grace.
- `setup` no longer copies the units into the user config when the package
  already provides them.

### Known limitations

- FTS5 has no native Russian stemming; `feagle` compensates with a stemmed
  column (Snowball), not a tokenizer.
- OCR is opt-in, CPU-heavy and needs `tesseract` plus language data.
- `sqlite-vec` is pre-1.0; `vec0` deletions do not shrink the file until
  `index --rebuild`, and a fresh vector table preallocates a 1024-row page group.
- Indexing is CPU-bound (~450 tokens/s on the reference laptop); a large corpus
  takes a while to index the first time.
- Publishing the package to the AUR is a manual step; the `PKGBUILD` is in the
  repository.

[0.1.0]: https://github.com/microwavedKing/feagle/releases/tag/v0.1.0
