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

### Changed

- `feagle search` now uses a running `queryd` when available and otherwise
  searches in-process (degrading to full-text without embeddings).

### Known limitations

- FTS5 has no Russian stemming; keyword search matches exact word forms.
- `sqlite-vec` is pre-1.0; `vec0` deletions do not shrink the file until
  `index --rebuild`.
- Scanned PDFs without a text layer yield no text (no OCR yet).
