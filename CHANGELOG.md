# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Project scaffolding: `pyproject.toml`, package layout under `src/feagle/`,
  license, this changelog.
- XDG path resolution (`paths.py`) and stderr logging (`log.py`) for journald.
- TOML configuration loader with defaults, merge, validation and a documented
  template (`config.py`).
- SQLite storage: schema, WAL pragmas, migrations and read-only connections
  (`db.py`).
- Indexer: minimal `.gitignore` matcher, filesystem scanner with filters and
  limits, text/code extraction with binary detection, a character-based chunker
  with page/line positions, and an incremental indexing pipeline with hashing,
  pruning and rebuild support.
- Full-text search over FTS5 with query sanitization (BM25 ranking).
- CLI: `feagle index` (`--root`, `--full`, `--rebuild`), `feagle search`
  (`--limit`, `--json`) and `feagle status` (`--json`).
- Embeddings: model presets with pinned SHA256, EmbeddingGemma 2 task prompts,
  an HTTP client for `/v1/embeddings` (batching, dimension discovery, MRL
  truncation with re-normalization), streamed model download with verification,
  and a `llama-server` supervisor (reuse via port file, lazy start, idle stop).
- Vector storage: `sqlite-vec` loading and a dimension-checked `chunks_vec`
  table (`db.py`), plus KNN search (`search/vec.py`).
- Hybrid search: Reciprocal Rank Fusion of BM25 and vector rankings, with
  AND-only FTS to avoid single-word OR noise.
- Query daemon: `queryd` NDJSON protocol over a Unix socket (0600) with a client,
  per-connection read-only database connections and idle-stop of the server.
- Indexing: batched chunk embeddings, an `embedded` flag, and `index --backfill`.
- CLI: semantic `search` modes (`--mode auto|hybrid|fts`), graceful degradation,
  `index --no-embed`, `index --backfill` and `feagle queryd`.
- PDF indexing: `pdftotext` extraction with per-page text (`\f` pagination),
  timeouts and a clear message when poppler-utils is missing; results carry the
  page number.
- `feagle open <path> [+N]` with configurable opener templates (`openers.py`),
  plus a `--dry-run` mode.
- Indexer daemon (`feagle indexerd`): `watchfiles` loop, periodic rescan,
  per-root rescan on change and battery/load pauses (`power.py`).
- systemd user units (`systemd/`) for the indexer and query daemon, with resource
  limits, and an installer (`units.py`).
- `feagle doctor`: environment checks with concrete fix hints.
- `feagle setup`: interactive first-run configuration (roots, launcher, model
  profile, model download, units, first index) with `--yes` and flags for
  non-interactive use.
- Russian stemming: a second FTS column holds Snowball stems of Cyrillic words;
  exact and stemmed rankings are fused with RRF (`stemming.py`, schema v2 with
  automatic migration); `[search].stemming`.
- OCR for scanned PDFs: pages without a text layer are rendered with `pdftoppm`
  and recognized with `tesseract` (`indexer/ocr.py`); configurable languages
  (default `rus+eng`), DPI, trigger threshold and page limit.
- Launcher adapters (`launchers.py`) for fuzzel, wofi, rofi, tofi and walker with
  auto-detection, a `stdout` fallback, and `feagle search --pick` which opens the
  chosen result.
- `feagle pick`: one launcher window with desktop applications (parsed from XDG
  `.desktop` entries) first and indexed files after them, so applications win
  when both match. `--query` ranks the files semantically;
  `search --pick --with-apps` mixes applications into search results.
- Search quality controls: `[search].max_distance` drops unrelated vector
  neighbours (an unrelated query no longer returns a full page of weak matches)
  and `[search].max_per_file` keeps one document from filling the result list.
- Legacy text encodings: non-UTF-8 files are decoded with a cp1251 (Cyrillic) /
  cp1252 / latin-1 fallback instead of becoming mojibake.
- Benchmarks: `feagle bench` (indexing throughput, query latency, memory,
  database size, MRR@10/Recall@20) and the `bench/` scripts
  (`gen_corpus.py`, `quality_eval.py`) on a deterministic synthetic corpus.
- `embeddinggemma-2-q4` model preset (from the unsloth mirror, SHA256-pinned
  and verified) — the new light default.

### Changed

- The default embedding model is now `embeddinggemma-2-q4`: measured 27 % faster
  indexing and ~65 MiB less memory than `q8` with identical retrieval quality.
  `bench` measurements are recorded in the README.

### Performance

- Stemming results are cached, cutting the indexer's own CPU by ~3.9× on
  Cyrillic-heavy text (profiled before/after; the model still dominates
  wall-clock).
- Hotkey examples for Hyprland (hyprlang and the new Lua config), Sway, niri,
  River and mango (TOML since 0.18) in the README.

### Changed

- `feagle search` now uses a running `queryd` when available and otherwise
  searches in-process (degrading to full-text without embeddings).

### Fixed

- systemd user units are written with an absolute `feagle` path, so a venv or
  pipx install can actually start them.
- The CLI configures logging: daemons log at INFO to journald, interactive
  commands stay quiet (previously INFO messages were lost entirely).
- `llama-server` runs with enough per-slot context for the largest chunk, and a
  chunk the server rejects no longer costs the whole file its vectors.
- Two daemons starting at the same time serialize on a lock file and share one
  `llama-server` instead of each spawning their own.
- The systemd units no longer let systemd manage the shared runtime directory
  (`RuntimeDirectory=feagle`): restarting one unit deleted the other's socket.
- `queryd` restarts the embeddings server on demand after a failed start or a
  crash; previously it stayed full-text-only until restarted by hand.

### Known limitations

- FTS5 has no Russian stemming natively; `feagle` compensates with a stemmed
  column (Snowball), not a native tokenizer.
- OCR is opt-in, CPU-heavy and needs `tesseract` plus language data.
- `sqlite-vec` is pre-1.0; `vec0` deletions do not shrink the file until
  `index --rebuild`.
- Packaging (PKGBUILD/AUR, CI, a tagged release) is not implemented yet.
