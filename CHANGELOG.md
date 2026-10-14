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

### Known limitations

- Semantic (vector) search and PDF extraction are not implemented yet.
- FTS5 has no Russian stemming; keyword search matches exact word forms.
