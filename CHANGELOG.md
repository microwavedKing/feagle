# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-10-19

Security, packaging and a round of fixes from an audit of the whole code base.
The query daemon previews only files that are in the index, removing a directory
from `[index].roots` removes it from the index too, and `feagle doctor` explains
the two settings that bite most often (a large embedding batch on a wide CPU
model, a remote `[embeddings].server_url`). Adds a manual page, shell
completions and freshly measured tables. No breaking changes; the two visible
differences are that a dropped root disappears from the index and that
`feagle setup` keeps timestamped backups.

### Security

- `queryd` now previews **only files that are in the index**. The `preview`
  operation used to read whatever path it was handed, so any local process able to
  open the 0600 socket could read any file of the account. The path is normalised
  the way the indexer stores it and must be a row in `files`; a symlink is refused
  while `[index].follow_symlinks = false` (the default).

### Added

- `README.md`: a "Privacy and security" section (EN + RU), and a matching
  "Модель безопасности" subsection in `ARCHITECTURE.md`.
- The model section documents the measured `embed_batch_size` caveat for the
  1024-dim alternative: on CPU a 16-chunk batch of long book chunks can exceed the
  client's 60-second timeout and leave files without vectors (81% coverage,
  measured); `embed_batch_size = 4` completes without timeouts.
- `feagle doctor` says the same thing as a warning when it sees a wide model
  (1024 dimensions) with a large `[index].embed_batch_size`, so the advice is not
  only in the README.
- A manual page, `man/feagle.1`: every command and option, the files and
  directories feagle uses, the environment variables, exit status, and the
  degradation and privacy notes.
- Shell completions for bash (`completions/feagle.bash`), zsh
  (`completions/_feagle`) and fish (`completions/feagle.fish`). The AUR package
  installs them, and `tests/test_cli_docs.py` fails when one of them (or the man
  page) misses a command or an option that `feagle --help` knows.
- The AUR package now installs the manual page and the completions, and its
  optional dependencies were brought up to date: `quickshell` (the recommended
  popup) and `whisper.cpp` (audio) were missing, OCR orientation data and media
  descriptions were clarified, and the dmenu launchers are now described as the
  `feagle pick` alternative they are.
- `bench/corpus_eval.py`: `--max-distance` to sweep the vector cutoff and `--db`
  to reuse one database across a sweep (it refuses a database built with another
  model or vector dimension). `bench/ocr_eval.py`: `--osd-pdf`/`--osd-page` and a
  three-rotation OSD table with the character error rate per rotation.

### Changed

- Development status is beta (`pyproject.toml`, the README status line).
- `scripts/release.py bump` also updates the version and the date in the man
  page header.
- A pass paused by the load or battery check is retried after five minutes even
  when periodic rescans are disabled (`rescan_interval_min = 0`); before, such a
  machine waited for the rescan interval (six hours by default) or for a file
  change.

### Fixed

- The database size reported by `feagle bench` (and by `bench/corpus_eval.py`,
  `bench/quality_eval.py`) now includes the write-ahead log instead of counting
  only the main file, so the published bytes-per-chunk figures are the real
  ones. On the book corpus this corrects `mrl_dim = 256` from "about half the
  size" to **28% smaller** (8.7 → 6.3 KB/chunk).
- `feagle bench` and `bench/quality_eval.py` clear the previous synthetic corpus
  before generating a new one. The default corpus directory is shared, so a
  leftover 1200-document corpus made a 60-document quality run index all 1200
  documents and report a much lower score.
- An HTML/XML file that cannot be parsed is now reported as a file error instead
  of being indexed as raw markup: tags would dilute its embedding and nothing
  would tell you that the page text was never extracted. The rest of the run is
  unaffected, as with any other extraction failure.
- An opener template may contain literal braces (`sh -c 'awk {print $1}'`) and a
  path may contain `{`/`}`; both used to go through `str.format` and could raise.
  Only `{path}`, `{line}`, `{page}` and `{time}` are substituted now (format specs
  still work), anything else passes through literally.
- Backfill no longer gives up on the whole queue because of one file: a file whose
  content cannot be embedded keeps `embedded=0` for a later pass, and the walk
  goes on. Only a *run* of failures ends the pass early — three in a row — which
  is what a dead embeddings server looks like, where every further attempt would
  cost a client timeout.
- A request to the query daemon is now bounded: a line over 256 KiB is refused
  instead of buffered, a search query over 4096 characters is refused, `embed`
  refuses more than 64 texts or one over 8192 characters, at most 16 connections
  are served at once (the next is refused right away) and a connection idle for
  120 s is closed. None of this changed what a normal client sees.
- `llama-server` startup is retried on a new port when it exits immediately, which
  is what a port taken between the free-port probe and the bind looks like. A
  server that stays alive but never becomes healthy is still an error after one
  attempt.
- A directory dropped from `[index].roots` is now removed from the index by the
  next pass over the configured roots. Its files used to stay in the database and
  keep showing up in search forever — wrong precisely when the directory was
  removed to stop indexing it. A pass with an explicit root list
  (`feagle index --root PATH`) and a pass that paused halfway still only touch
  what they scanned, and a root that is merely unavailable (an unmounted drive)
  keeps its files.
- A pass paused by the load or battery check no longer deletes the files it did
  not reach yet. Reconciling the index walks the whole `files` table and drops
  what the scan did not see, so a pause in the middle of a scan used to empty the
  rest of the index (the next pass would rebuild it, but the work was wasted and
  the index was wrong in the meantime).
- `feagle index --full` also cleans up files that vanished, like a normal pass,
  instead of skipping the reconciliation.
- The indexer reacts to a changed root list right away: dropping a directory from
  `[index].roots` while the daemon is running now runs the pass that removes its
  files, instead of waiting for the next periodic rescan (six hours by default).
- `feagle setup` keeps every backup: a fixed `config.toml.bak` meant the second
  run overwrote the backup of the first. The copy is now named
  `config.toml.bak-YYYYmmdd-HHMMSS` (with a suffix if two runs share a second).
- A `Terminal=true` desktop entry now starts through its terminal's own syntax:
  `konsole -e`, `gnome-terminal --`, `wezterm start --`, `xterm`/`alacritty -e`,
  and kitty/foot take the command positionally. Before, only `xterm` and
  `alacritty` got the `-e`, so konsole and gnome-terminal opened without the
  command at all.
- The query protocol checks the fields it is given instead of trusting them: a
  `limit` that is not a number, a `mode` outside the three known ones, `texts`
  as a bare string (which used to be turned into a list of single characters) or
  a `line`/`page`/`apps_limit` of the wrong type now answer `ok:false` with a
  reason. Numeric strings are still accepted, so nothing a normal client sends
  changed.
- `preview` accepts a path in the form the indexer stored (the resolved one) as
  well as the literal one, so a file indexed through a symlinked root can be
  previewed. The allowlist is unchanged: only files that are rows in the index
  are served.
- A `llama-server` that had to be killed is now reaped (and one the supervisor
  no longer tracks is stopped before a new one is spawned). Without the extra
  `wait` the process stayed a zombie until the daemon exited, and the pid file
  kept pointing at it.
- Pointing `[embeddings].server_url` at another machine now warns when the
  config is loaded, and `feagle doctor` reports it (info for `https://`, warn for
  plain `http://`). Every query and every indexed document goes there; that is
  the owner's choice, but it should not be a surprise.
- The model comparison in the README is now measured on one corpus (400
  synthetic documents, 434 chunks, 400 queries) instead of mixing runs of
  different sizes, and it no longer claims that q4 and q8 have **identical**
  retrieval quality: on that corpus q8 scores higher (MRR@10 0.938 vs 0.856,
  Recall@20 1.000 vs 0.917). The two still tie on the 60-document corpus (0.750
  each) and on the real book corpus (1.000 each). The latency rows were
  re-measured on an idle machine: hybrid p50/p95 58/81 ms (embedding 55 ms,
  search 2.7 ms), full-text 1.8/6.7 ms. The Qwen3 book row stays from the earlier
  230-chunk corpus: a full re-measure at a batch of 4 was stopped on purpose, as
  the corpora already disagree and EmbeddingGemma is ahead on real text.

## [0.3.0] - 2026-10-18

Quality and release tooling, plus two search bugs found by using the thing. No
breaking changes; existing configs keep their own `include_ext` list.

### Added

- `.html`, `.htm`, `.xml` and `.log` are indexed by default. HTML and XML are
  reduced to their visible text first (markup dropped, `<script>`/`<style>`/
  `<head>` ignored), so tags no longer dilute the embeddings.
- `scripts/release.py`: `bump X.Y.Z` writes the version into `PKGBUILD`,
  `.SRCINFO` and the documentation examples, `sum X.Y.Z` downloads the tag
  archive and pins its checksum. `pyproject.toml` now reads the version from
  `src/feagle/__init__.py` through hatchling, so the wheel, the package and
  `feagle --version` can no longer disagree.
- Configuration warnings: an explicit `[search].max_distance` that disagrees
  with the model preset's measured distance scale, and an `mrl_dim` larger than
  the model's own dimension. `feagle doctor` reports the same mismatch as a hint
  on the model check.
- `pytest-cov` with a coverage gate in CI (~85%), a test for `metrics.py`, and
  `mypy` over `src/` and `scripts/` as a CI step.
- A semantic CI job (weekly + manual) that builds `llama-server` from llama.cpp
  master, downloads the model and runs the end-to-end test for real.

### Fixed

- Hybrid search could return **less** than the keyword search: when every vector
  neighbour was filtered by `max_distance` and the AND-only text ranking was
  empty, it returned nothing where the full-text pass had found hits — the
  launcher's list then went blank. It now falls back to the OR ranking, but only
  when nothing else survived, so the semantic side still decides whenever it has
  an opinion.
- The launcher no longer replaces a non-empty result list with an empty semantic
  reply.
- Image and video search never worked in `indexerd`: the wrapper that restarts a
  dropped embeddings server did not forward `embed_images`, so every image was
  indexed without a vector. `feagle index` was unaffected, which is why it went
  unnoticed.
- `queryd` no longer prints a traceback when a client disconnects mid-reply.

### Changed

- `ruff` also runs `BLE`, `RUF`, `PERF`, `RET` and `TCH` (with `RUF001-003`
  ignored: the Russian text and typography are intentional).
- Documentation: README gained a quick start and a contents list; the launcher,
  install and architecture documents were refreshed.

## [0.2.1] - 2026-10-18

Polish release: the launcher grew a real result list and a PDF preview, and the
documentation gained a quick start.

### Added

- The launcher groups results under `Applications` / `Files`, shows a thin
  scrollbar with an "*N* more below" hint, and has clickable `All` / `Apps` /
  `Files` filter chips next to the search field (the same as `Tab`).
- The launcher preview pane scrolls with the wheel, wraps long lines and
  emphasises **every** query word in the shown lines (source HTML is escaped).
- Launcher keys: `Ctrl+U` / `Ctrl+W` / `Ctrl+A` for line editing, `Ctrl+N` /
  `Ctrl+P` to move, `Ctrl+C` to copy the file's path (or the application's
  command) with a confirmation in the footer, and `Ctrl+Enter` to open the folder
  that contains the file.
- Launcher theming and layout are now configurable: `lightTheme`, `cardWidth`,
  `cardPreviewWidth`, `cardHeight`, `cardPreviewHeight`, `previewWidth`,
  `topMarginRatio`; displayed paths use `~` for `$HOME` and the card resizes with
  an animation.
- `queryd`'s `preview` renders a **PDF page** (`page` plus `chunk_id` to anchor
  the window on the text that matched), caching rendered pages per
  (file, mtime, page); without poppler the reply degrades to file metadata.
- README: a three-command quick start and a contents list (both language halves).

### Changed

- `quickshell/README.md`, `docs/INSTALL.ru.md` and the README launcher sections
  document the hotkey table, the new UI and the PDF preview.

## [0.2.0] - 2026-10-18

Feature release: opt-in media search (images, video, audio), image OCR, a
single-window Quickshell launcher with previews, and indexer/OCR performance
work with measured numbers in the README.

### Added

- Image OCR: with `[index].ocr_images = true`, `feagle` indexes `png`, `jpg`,
  `jpeg`, `webp` and `tiff` files by running `tesseract` on them directly. Only
  `tesseract` is needed (no renderer); scanned PDF pages still also need
  `pdftoppm`. Recognized text is searchable like any other document, and image
  chunks carry no line/page position.
- Image search (opt-in): with `[media].images = true`, image files are embedded
  through the multimodal projector (`mmproj`, preset `embeddinggemma-2-mmproj-q8`)
  so a text query can find a picture. Images are downscaled (`ffmpeg`) before
  embedding, at most `[media].images_per_run` are done per indexing pass, and the
  flag is off by default (it makes the shared server use ~1 GiB of RAM).
- Video search (opt-in): with `[media].video = true`, `ffmpeg` extracts frames
  (`video_frame_interval`, up to `video_max_frames`) and they are embedded like
  images, so a text query finds the right moment. Frame timestamps are stored in
  the schema-v3 `chunks.time_ms`; results carry a timestamp and `feagle open
  clip.mp4 +30` treats `+30` as seconds. The `{time}` opener placeholder expands
  to seconds (for example `mp4 = "mpv --start={time} {path}"`).
- Video OCR (opt-in): with `[media].video_ocr = true`, each extracted video frame
  is also run through `tesseract`, so text *shown* in the video becomes searchable
  (with its timestamp) in addition to the semantic frame embeddings.
- Audio search (opt-in): with `[media].audio = true`, audio is converted with
  `ffmpeg` and transcribed with `whisper.cpp` (`whisper-cli`), and the transcript
  is indexed as text (full-text and semantic) with per-segment timestamps. Audio
  needs no `mmproj`, and full-text search works without `llama-server`.
- Launcher API: `queryd` gained `warmup` (start the embedder in the background,
  never blocking the caller) and `launcher` (a ready-to-render mixed list:
  applications first, then file hits with snippet and position). Applications are
  scored by name (`apps.match_apps`) and discovered once, then cached in memory.
  An example Quickshell popup that uses it lives in `quickshell/`.
- `feagle setup` can enable and provision the media features: the new flags
  `--media-images`, `--media-video` and `--media-audio` (or the interactive
  prompts) turn on the matching `[media]` options and download the required
  models — the multimodal projector for images/video and a `whisper.cpp` model
  (`ggml-base.bin`, pinned) for audio. Both downloads verify their SHA256.
- `feagle doctor` reports the devices `llama-server` can offload to
  (`--list-devices`), so GPU offload availability is visible before tuning
  `[embeddings].extra_args`.
- A single-window Quickshell launcher: `quickshell/FeagleLauncher.qml` shows
  applications first, then file hits, with keyword results on every keystroke and
  a semantic refresh a moment later — no second "ask for a query" window. While
  the query is empty it lists every application (browse mode), and when the icon
  theme has no icon it draws a small badge (application initial or file
  extension) instead of a missing texture. The protocol is factored into a
  reusable `quickshell/FeagleSearch.qml` backend so feagle search can be added to
  any existing Quickshell launcher; `quickshell/README.md` documents the
  component API and the wire protocol.
- `queryd`'s `launcher` results for applications now carry `icon` (the `.desktop`
  `Icon=` name), so a launcher can show real application icons.
- `queryd` gained `preview` (a small preview of one file: numbered context lines
  around a match, an image marker, or file metadata) and `used` (remember that an
  application was launched). Application ranking adds a bounded *frecency* bonus —
  how often and how recently an app was launched, decaying with a 30-day half-life
  — so a frequently used app comes first inside a match tier and in the
  empty-query browse list, while never outranking a better name match. History
  lives in `$XDG_STATE_HOME/feagle/frecency.json`, and `feagle pick` records
  launches there too.
- The Quickshell launcher shows a **preview pane** for the selected file (numbered
  context lines with the match highlighted, or the image itself), filters results
  with `Tab` (all / applications / files) and can open pre-filled from a hotkey
  (`qs ipc -p … call feagle-launcher search "…"`). Replies echo an optional `req`
  tag, so one socket can mix request kinds.
- OCR tuning: `[index].ocr_workers` (1 or 2) recognizes scanned PDF pages in a
  process pool — `tesseract` is single-threaded, and two workers measured **1.78×**
  faster (0.97 s → 0.52 s per page) on the synthetic scan. `[index].ocr_psm`
  selects tesseract's page segmentation mode, and `[index].ocr_osd` uses OSD
  (`--psm 1` with the `osd` language) so a rotated scan is detected and undone;
  `feagle doctor` warns when `ocr_osd` is on but `tesseract-data-osd` is missing.
  `bench/ocr_eval.py` measures OCR throughput and quality (CER/WER, word
  confidence) across `--psm` and DPI values.
- Qwen3-Embedding-0.6B preset (`qwen3-embedding-0.6b-q8`, 1024-dim) as a fallback
  model: `llama-server` gets `--pooling last`, queries use the model card's
  `Instruct: …\nQuery: …` prefix while documents take none (prompt families are now
  part of the preset, so a new model brings its own prompts), and
  `[embeddings].mrl_dim` accepts 1024.
- `bench/corpus_eval.py` measures retrieval quality (MRR@10 / Recall@10/20) on a
  corpus that is already on disk, given a JSON query file; `bench/quality_eval.py`
  gained `--model` / `--mrl-dim` overrides and now reports the database size.

### Changed

- `indexerd` re-indexes only the paths the watcher reports instead of walking the
  enclosing root (measured 185 ms → 2 ms, **86×**, on a 3000-file root). Deletions
  in a batch drop the file's rows directly; the periodic full rescan stays as the
  safety net for what the watcher might miss.
- `indexerd` re-reads the config between passes, so roots, limits, OCR settings and
  the rescan interval apply **without a restart** (a root change restarts the
  watcher; an invalid config is logged and the previous one is kept). `--root`
  disables the reload, since it overrides the configured roots.
- Media indexing now uses its own `llama-server` with `--mmproj` (coordination
  files `llama-media.*`), owned by the indexer and unloaded when idle. `queryd`
  keeps a light text-only server, so enabling images/video no longer makes the
  query daemon hold the projector (~1 GiB) for text searches.

### Fixed

- Switching `[embeddings].model` to another preset no longer silently disables
  semantic results. `[search].max_distance` was tuned on EmbeddingGemma, and
  Qwen3-Embedding puts even *correct* pairs much further apart, so the 0.8 cutoff
  dropped every vector neighbour and the hybrid search degenerated to full-text
  only (measured MRR@10 0.000 on the synthetic corpus and 0.077 on the books
  corpus, against 0.75/1.000 for EmbeddingGemma). A model preset can now suggest
  its own cutoff, applied while the config keeps the built-in default.

## [0.1.1] - 2026-10-15

Bug-fix release.

### Fixed

- `indexerd` no longer stays stuck with a dead or empty embedder: it retries the
  embeddings service on every pass and resets and restarts the server on
  `ServerUnavailable`, so a server that was down at startup or unloaded by
  `queryd` no longer leaves new files permanently `embedded=0`. Files that still
  lack vectors are backfilled at the end of a pass.
- An `llama-server` owned by `indexerd` is unloaded when idle instead of staying
  in memory (~460 MiB) forever.
- Picking a search result opens the selected chunk: several chunks of the same
  file (for example PDF pages) are keyed separately, so the second or third
  result no longer opens the first chunk.
- `feagle pick --query` uses a running `queryd` (and its warm embeddings)
  instead of always searching in-process.
- `queryd`'s `embed` operation accepts optional document titles instead of
  clearing them to `null`.
- `feagle setup` defaults the model prompt to the configured model and no longer
  announces features as "coming in a later phase".
- `feagle doctor` detects batteries by the `/sys` `type` file, so it also works
  with batteries named `CMB0`, `BATT` or `macsmc-battery`.

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

[0.1.1]: https://github.com/microwavedKing/feagle/releases/tag/v0.1.1
[0.1.0]: https://github.com/microwavedKing/feagle/releases/tag/v0.1.0

---

## 0.4.0 (по-русски) — 2026-10-19

Безопасность, упаковка и серия исправлений по итогам аудита всего кода. Демон
запросов отдаёт превью только для файлов из индекса, удаление каталога из
`[index].roots` убирает его и из индекса, а `feagle doctor` объясняет две настройки,
которые чаще всего кусаются (большой батч эмбеддинга на широкой CPU-модели и внешний
`[embeddings].server_url`). Добавлены man-страница, комплишены и перемеренные таблицы.
Ломающих изменений нет; заметны две вещи — убранный из `[index].roots` каталог
исчезает из индекса, а `feagle setup` хранит бэкапы с таймстампом.

### Безопасность

- `queryd` отдаёт превью **только файлов из индекса**. Операция `preview` читала любой
  переданный путь, поэтому любой локальный процесс, открывший сокет 0600, мог
  прочитать любой файл учётной записи. Теперь путь нормализуется так же, как его
  хранит индексатор, и обязан быть строкой в `files`; симлинк отвергается, пока
  `[index].follow_symlinks = false` (значение по умолчанию).

### Добавлено

- `README.md`: раздел «Приватность и безопасность» (EN + RU) и соответствующий
  подраздел «Модель безопасности» в `ARCHITECTURE.md`.
- В разделе про модели зафиксирован измеренный нюанс `embed_batch_size` для
  1024-мерной альтернативы: на CPU батч из 16 длинных книжных чанков не влезает в
  60-секундный таймаут клиента, и файлы остаются без векторов (81 % покрытия,
  замер); с `embed_batch_size = 4` прогон идёт без таймаутов.
- `feagle doctor` говорит то же самое предупреждением, когда видит широкую модель
  (1024 измерения) с большим `[index].embed_batch_size` — совет теперь не только в
  README.
- Man-страница `man/feagle.1`: все команды и опции, файлы и каталоги, переменные
  окружения, коды выхода, режим деградации и приватность.
- Комплишены для bash (`completions/feagle.bash`), zsh (`completions/_feagle`) и fish
  (`completions/feagle.fish`). AUR-пакет ставит их, а `tests/test_cli_docs.py` падает,
  если в них (или в man-странице) нет команды или опции, которая есть в `feagle
  --help`.
- AUR-пакет теперь ставит man-страницу и комплишены, а его optdepends приведены в
  порядок: не хватало `quickshell` (рекомендуемый попап) и `whisper.cpp` (аудио),
  уточнены описания OCR-данных и медиа, а dmenu-лаунчеры подписаны как альтернатива
  для `feagle pick` — чем они и являются.
- `bench/corpus_eval.py`: `--max-distance` для свипа порога и `--db` для
  переиспользования одной базы во всём свипе (база с другой моделью или
  размерностью отвергается). `bench/ocr_eval.py`: `--osd-pdf`/`--osd-page` и таблица
  OSD по трём поворотам с CER на каждый.

### Изменено

- Статус разработки — бета (`pyproject.toml`, строка статуса в README).
- `scripts/release.py bump` обновляет также версию и дату в заголовке man-страницы.
- Проход, приостановленный по нагрузке или батарее, повторяется через пять минут
  даже при выключенном периодическом rescan (`rescan_interval_min = 0`); раньше
  такая машина ждала интервала rescan (по умолчанию шесть часов) или изменения файла.

### Исправлено

- Размер БД, который печатает `feagle bench` (и `bench/corpus_eval.py`,
  `bench/quality_eval.py`), теперь считается вместе с write-ahead log, а не только
  по главному файлу, поэтому опубликованные Б/чанк — настоящие. На корпусе книг это
  исправляет `mrl_dim = 256` с «примерно вдвое меньше» на **на 28 % меньше**
  (8.7 → 6.3 КБ/чанк).
- `feagle bench` и `bench/quality_eval.py` очищают прошлый синтетический корпус перед
  генерацией нового. Каталог корпуса по умолчанию общий, поэтому оставшийся корпус на
  1200 документов заставлял прогон «на 60 документов» индексировать все 1200 и
  показывать заметно худший результат.
- HTML/XML, который не удалось разобрать, теперь помечается ошибкой файла, а не
  индексируется сырой разметкой: теги разбавляли бы его эмбеддинг, и никто бы не
  узнал, что текст страницы не извлечён. Остальной проход это не ломает — как и
  любая другая ошибка извлечения.
- В шаблоне открывалки теперь могут быть литеральные фигурные скобки
  (`sh -c 'awk {print $1}'`), а в пути — `{`/`}`; раньше и то и другое шло через
  `str.format` и могло упасть. Подставляются только `{path}`, `{line}`, `{page}` и
  `{time}` (формат-спецификации по-прежнему работают), остальное остаётся как есть.
- Backfill больше не бросает всю очередь из-за одного файла: файл, содержимое
  которого не эмбеддится, остаётся с `embedded=0` до следующего прохода, а обход
  продолжается. Проход прерывается только после серии неудач — трёх подряд: так
  выглядит мёртвый сервер эмбеддингов, где каждая следующая попытка стоила бы
  клиентского таймаута.
- Запрос к демону запросов теперь ограничен: строка длиннее 256 КиБ отклоняется, а
  не буферизуется, поисковый запрос длиннее 4096 символов отклоняется, `embed`
  отклоняет больше 64 текстов или текст длиннее 8192 символов, одновременно
  обслуживается не больше 16 соединений (следующее отклоняется сразу), а соединение,
  молчащее 120 с, закрывается. Для обычного клиента не изменилось ничего.
- Старт `llama-server` повторяется на новом порту, если процесс вышел сразу — так
  выглядит порт, перехваченный между пробой свободного порта и bind. Сервер, который
  жив, но так и не стал здоровым, по-прежнему считается ошибкой после одной попытки.
- Каталог, убранный из `[index].roots`, теперь удаляется из индекса на следующем
  проходе по настроенным корням. Раньше его файлы навсегда оставались в базе и
  продолжали находиться поиском — как раз когда папку и убирают из конфига, чтобы
  прекратить её индексировать. Проход с явным списком корней
  (`feagle index --root PATH`) и проход, прерванный паузой, по-прежнему трогают
  только то, что просканировали, а корень, который просто недоступен
  (несмонтированный диск), сохраняет свои файлы.
- Проход, прерванный паузой по нагрузке или батарее, больше не удаляет файлы, до
  которых не дошёл. Сверка индекса обходит всю таблицу `files` и выкидывает всё,
  чего не видела, поэтому пауза посреди сканирования очищала остаток индекса
  (следующий проход его перестраивал, но работа тратилась зря, а до тех пор индекс
  был неверным).
- `feagle index --full` теперь тоже чистит исчезнувшие файлы, как обычный проход, а
  не пропускает сверку.
- Индексатор реагирует на смену набора корней сразу: удаление каталога из
  `[index].roots` на работающем демоне запускает проход, который убирает его файлы,
  а не ждёт планового рескана (по умолчанию шесть часов).
- `feagle setup` сохраняет **все** бэкапы: из-за фиксированного имени
  `config.toml.bak` второй прогон затирал копию первого. Теперь бэкап называется
  `config.toml.bak-ГГГГММДД-ЧЧММСС` (при двух прогонах в одну секунду — с суффиксом).
- Запись `.desktop` с `Terminal=true` запускается так, как требует её терминал:
  `konsole -e`, `gnome-terminal --`, `wezterm start --`, `xterm`/`alacritty -e`,
  а kitty/foot принимают команду позиционно. Раньше `-e` получали только `xterm` и
  `alacritty`, поэтому `konsole` и `gnome-terminal` открывались **без** команды.
- Протокол запросов проверяет типы полей, а не доверяет им: `limit` не числом,
  `mode` вне трёх известных, `texts` одной строкой (она превращалась в список
  отдельных символов) или `line`/`page`/`apps_limit` не того типа теперь получают
  `ok:false` с причиной. Числовые строки по-прежнему принимаются, так что для
  обычного клиента ничего не изменилось.
- `preview` принимает путь и в том виде, в каком его хранит индексатор
  (разрешённый), и в исходном, поэтому файл, проиндексированный через
  симлинкованный корень, превьюится. Allowlist не изменился: отдаются только файлы
  из индекса.
- Убитый `llama-server` теперь ещё и собирается reap'ом (а процесс, который
  супервизор больше не отслеживает, останавливается перед запуском нового). Без
  второго `wait` процесс оставался зомби до выхода демона, и pid-файл продолжал на
  него указывать.
- Указание чужой машины в `[embeddings].server_url` теперь предупреждает при
  загрузке конфига, а `feagle doctor` показывает это отдельной строкой (info для
  `https://`, warn для обычного `http://`). Туда уходит каждый запрос и каждый
  документ при индексации — это осознанный выбор владельца, но не сюрприз.
- Сравнение моделей в README теперь измерено на одном корпусе (400 синтетических
  документов, 434 чанка, 400 запросов), а не на прогонах разного размера, и больше не
  утверждает **одинаковое** качество q4 и q8: на этом корпусе q8 выше (MRR@10 0.938
  против 0.856, Recall@20 1.000 против 0.917). На 60 документах оба дают 0.750, на
  реальных книгах — 1.000. Строки латентности перемерены на простое: гибрид p50/p95
  58/81 мс (эмбеддинг 55 мс, поиск 2.7 мс), полнотекст 1.8/6.7 мс. Строка Qwen3 по
  книгам остаётся с прежнего корпуса (230 чанков): полный перемер при батче 4
  остановлен — корпуса и так расходятся, а на реальном тексте впереди EmbeddingGemma.

## 0.3.0 (по-русски) — 2026-10-18

Качество и релиз-инструменты плюс два бага поиска, найденных на практике. Ломающих
изменений нет; в существующих конфигах свой список `include_ext` остаётся как есть.

### Добавлено

- По умолчанию индексируются `.html`, `.htm`, `.xml` и `.log`. У HTML/XML сначала
  снимается разметка (`<script>`/`<style>`/`<head>` игнорируются), поэтому теги
  больше не разбавляют эмбеддинги.
- `scripts/release.py`: `bump X.Y.Z` вписывает версию в `PKGBUILD`, `.SRCINFO` и
  примеры в документации, `sum X.Y.Z` скачивает архив тега и закрепляет его
  контрольную сумму. `pyproject.toml` теперь читает версию из
  `src/feagle/__init__.py` через hatchling, поэтому колесо, пакет и
  `feagle --version` не могут разойтись.
- Предупреждения конфига: явный `[search].max_distance`, не сходящийся с измеренной
  шкалой дистанций пресета модели, и `mrl_dim` больше размерности модели.
  `feagle doctor` показывает ту же нестыковку подсказкой к проверке модели.
- `pytest-cov` с порогом покрытия в CI (~85%), тест на `metrics.py` и `mypy` по
  `src/` и `scripts/` отдельным шагом CI.
- Задание семантического CI (раз в неделю + вручную): собирает `llama-server` из
  master llama.cpp, качает модель и прогоняет сквозной тест по-настоящему.

### Исправлено

- Гибридный поиск мог вернуть **меньше**, чем поиск по словам: когда все векторные
  соседи отсекались `max_distance`, а AND-ранжирование по тексту было пустым, он
  возвращал ничего там, где полнотекстовая фаза находила совпадения — список в
  лаунчере при этом пустел. Теперь он добирает OR-ранжирование, но только если
  ничего другого не осталось, поэтому семантика по-прежнему решает, когда у неё
  есть мнение.
- Лаунчер больше не заменяет непустой список результатов пустым ответом семантики.
- Поиск картинок и видео в `indexerd` не работал никогда: обёртка, перезапускающая
  упавший сервер эмбеддингов, не пробрасывала `embed_images`, поэтому каждая
  картинка индексировалась без вектора. `feagle index` был не затронут — потому
  баг и не всплывал.
- `queryd` больше не печатает трассировку, когда клиент обрывает соединение в
  момент ответа.

### Изменено

- `ruff` дополнительно включает `BLE`, `RUF`, `PERF`, `RET` и `TCH` (с
  `ignore RUF001-003`: русский текст и типографика намеренны).
- Документация: в README появились быстрый старт и оглавление; разделы про лаунчер,
  установку и архитектуру обновлены.

## 0.2.1 (по-русски) — 2026-10-18

Релиз-полировка: у лаунчера появился настоящий список результатов и превью
страниц PDF, а в документации — быстрый старт.

### Добавлено

- Лаунчер группирует результаты под заголовками `Applications` / `Files`,
  показывает тонкую полосу прокрутки с подсказкой «ещё *N* ниже» и кликабельные
  чипы фильтра `All` / `Apps` / `Files` рядом с полем поиска (то же, что `Tab`).
- Панель превью прокручивается колесом, переносит длинные строки и подсвечивает
  **все** слова запроса в показанных строках (HTML из исходника экранируется).
- Клавиши лаунчера: `Ctrl+U` / `Ctrl+W` / `Ctrl+A` для правки строки, `Ctrl+N` /
  `Ctrl+P` для перемещения, `Ctrl+C` — скопировать путь файла (или команду
  приложения) с подтверждением в футере, `Ctrl+Enter` — открыть каталог файла.
- Тема и размеры лаунчера настраиваются: `lightTheme`, `cardWidth`,
  `cardPreviewWidth`, `cardHeight`, `cardPreviewHeight`, `previewWidth`,
  `topMarginRatio`; в путях `$HOME` показывается как `~`, карточка меняет размер
  с анимацией.
- `preview` в `queryd` рендерит **страницу PDF** (`page` и `chunk_id`, чтобы
  привязаться к совпавшему тексту), кэшируя отрендеренные страницы по
  (файл, mtime, страница); без poppler ответ деградирует до метаданных файла.
- README: быстрый старт из трёх команд и оглавление (в обеих языковых частях).

### Изменено

- `quickshell/README.md`, `docs/INSTALL.ru.md` и разделы README про лаунчер
  описывают таблицу клавиш, новый интерфейс и превью PDF.

## 0.2.0 (по-русски) — 2026-10-18

Релиз с новыми функциями: опциональный поиск по медиа (картинки, видео,
аудио), OCR картинок, лаунчер в одно окно на Quickshell с превью, а также
ускорение индексатора и OCR — с замерами в README.

### Добавлено

- OCR картинок: при `[index].ocr_images = true` `feagle` индексирует `png`,
  `jpg`, `jpeg`, `webp` и `tiff`, распознавая их напрямую через `tesseract`.
  Достаточно одного `tesseract` (без рендера); страницам сканированных PDF
  по-прежнему нужен ещё `pdftoppm`. Распознанный текст ищется как обычный
  документ; у чанков картинок нет номера строки/страницы.
- Поиск картинок (опционально): при `[media].images = true` картинки эмбеддятся
  через мультимодальный проектор (`mmproj`, пресет `embeddinggemma-2-mmproj-q8`),
  поэтому текстовый запрос может найти изображение. Картинки уменьшаются
  (`ffmpeg`) перед эмбеддингом, за проход обрабатывается не более
  `[media].images_per_run`, а флаг по умолчанию выключен (иначе общий сервер
  занимает ~1 ГиБ RAM).
- Поиск видео (опционально): при `[media].video = true` `ffmpeg` извлекает кадры
  (`video_frame_interval`, не более `video_max_frames`) и они эмбеддятся как
  картинки, поэтому текстовый запрос находит нужный момент. Таймкоды кадров
  хранятся в `chunks.time_ms` (схема v3); у результата есть таймкод, а
  `feagle open clip.mp4 +30` трактует `+30` как секунды. Плейсхолдер `{time}` в
  `[openers]` раскрывается в секунды (например,
  `mp4 = "mpv --start={time} {path}"`).
- OCR кадров видео (опционально): при `[media].video_ocr = true` каждый извлечённый
  кадр дополнительно прогоняется через `tesseract`, поэтому текст, *показанный* в
  видео, становится находимым (со своим таймкодом) вдобавок к семантике кадров.
- Поиск по аудио (опционально): при `[media].audio = true` аудио конвертируется
  через `ffmpeg` и транскрибируется `whisper.cpp` (`whisper-cli`), а транскрипт
  индексируется как текст (полнотекстово и семантически) с таймкодами сегментов.
  Аудио не требует `mmproj`, а полнотекстовый поиск работает и без `llama-server`.
- API лаунчера: у `queryd` появились `warmup` (запуск эмбеддера в фоне, не блокируя
  вызов) и `launcher` (готовый смешанный список: сначала приложения, затем файлы
  со сниппетом и позицией). Приложения скорингятся по имени (`apps.match_apps`) и
  разбираются один раз, затем кэшируются в памяти. Пример попапа на Quickshell —
  в `quickshell/`.

- `feagle setup` умеет включать и ставить медиа-функции: флаги `--media-images`,
  `--media-video` и `--media-audio` (или интерактивные вопросы) включают
  соответствующие опции `[media]` и качают нужные модели — мультимодальный
  проектор для картинок/видео и модель `whisper.cpp` (`ggml-base.bin`,
  закреплена) для аудио. Обе загрузки проверяются по SHA256.
- `feagle doctor` показывает устройства, на которые `llama-server` может выгружать
  вычисления (`--list-devices`), чтобы доступность GPU-offload была видна до
  правки `[embeddings].extra_args`.
- Лаунчер в одно окно на Quickshell (`quickshell/FeagleLauncher.qml`): приложения
  первыми, затем файлы, ключевые результаты на каждое нажатие и семантическое
  обновление через мгновение — без второго окна «спросить запрос». При пустом
  запросе перечисляются все приложения (режим обзора), а если в теме иконок нет
  нужной, рисуется бейдж (первая буква приложения или расширение файла) вместо
  «битой» текстуры. Протокол вынесен в переиспользуемый
  `quickshell/FeagleSearch.qml`, поэтому feagle можно встроить и в свой
  QS-лаунчер; `quickshell/README.md` описывает API компонента и протокол сокета.
- Ответы `launcher` для приложений теперь несут `icon` (имя `Icon=` из `.desktop`),
  так что лаунчер показывает настоящие иконки приложений.
- `queryd` получил `preview` (небольшое превью файла: нумерованные строки вокруг
  совпадения, маркер картинки или метаданные) и `used` (запомнить запуск
  приложения). Скоринг приложений добавляет ограниченный бонус *frecency* — как
  часто и как давно приложение запускали, с полураспадом 30 дней, — поэтому
  часто используемое приложение идёт первым внутри тира совпадений и в списке
  обзора, но никогда не обгоняет лучшее совпадение по имени. История лежит в
  `$XDG_STATE_HOME/feagle/frecency.json`, `feagle pick` тоже пишет туда.
- В лаунчере появились панель превью выбранного файла, фильтр результатов по
  `Tab` (все / приложения / файлы) и запуск с готовым запросом
  (`qs ipc -p … call feagle-launcher search "…"`). Ответы эхо-повторяют
  необязательный тег `req`, поэтому один сокет может смешивать виды запросов.
- Настройка OCR: `[index].ocr_workers` (1 или 2) распознаёт страницы сканированных
  PDF в пуле процессов — `tesseract` однопоточный, и два воркера дали **1.78×**
  (0.97 → 0.52 с на страницу). `[index].ocr_psm` задаёт режим сегментации
  tesseract, `[index].ocr_osd` включает OSD (`--psm 1` с языком `osd`), чтобы
  повёрнутый скан определялся и разворачивался; `doctor` предупредит, если
  `ocr_osd` включён без `tesseract-data-osd`. `bench/ocr_eval.py` меряет скорость
  и качество (CER/WER, уверенность в словах).
- Пресет `qwen3-embedding-0.6b-q8` (1024 измерения) как запасная модель: сервер
  получает `--pooling last`, запросы идут с префиксом `Instruct: …\nQuery: …`,
  документы — без него (семейства промптов теперь часть пресета), а `mrl_dim`
  принимает 1024.
- `bench/corpus_eval.py` — качество (MRR@10 / Recall@10/20) на корпусе, который уже
  есть на диске, по JSON-файлу запросов; `bench/quality_eval.py` получил
  `--model`/`--mrl-dim` и печатает размер базы.

### Изменено

- Индексация медиа теперь использует свой `llama-server` с `--mmproj` (файлы
  координации `llama-media.*`), которым владеет индексатор и выгружает по простою.
  `queryd` держит лёгкий текстовый сервер, поэтому включение картинок/видео больше
  не заставляет демон запросов держать проектор (~1 ГиБ) ради текстового поиска.

- `indexerd` переиндексирует только пути, о которых сообщил вотчер, вместо обхода
  охватывающего корня (замер: 185 мс → 2 мс, **86×**, на корне с 3000 файлов).
  Удаления в батче снимают строки файла сразу; периодическое полное
  пересканирование остаётся страховкой от пропущенных удалений.
- `indexerd` перечитывает конфиг между проходами: корни, лимиты, настройки OCR и
  интервал rescan применяются **без перезапуска** (смена корней перезапускает
  вотчер; битый конфиг логируется, а прежний продолжает работать). `--root`
  отключает reload, так как он перекрывает настроенные корни.

### Исправлено

- Смена `[embeddings].model` на другой пресет больше не отключает семантику
  молча. `[search].max_distance` был подобран под EmbeddingGemma, а
  Qwen3-Embedding разносит даже *правильные* пары куда дальше, поэтому порог 0.8
  отбрасывал всех векторных соседей и гибридный поиск вырождался в полнотекстовый
  (замер: MRR@10 0.000 на синтетике и 0.077 на книгах против 0.75/1.000 у
  EmbeddingGemma). Теперь пресет может предложить свой порог, который применяется,
  пока в конфиге стоит встроенное значение.

## 0.1.1 (по-русски)

Релиз исправлений.

### Исправлено

- `indexerd` больше не «застревает» со сломанным или пустым эмбеддером: он
  запрашивает сервис эмбеддингов на каждом проходе и при `ServerUnavailable`
  делает reset и перезапуск сервера, поэтому сервер, недоступный на старте или
  выгруженный `queryd`, больше не оставляет новые файлы навсегда с `embedded=0`.
  Файлы без векторов добираются в конце прохода.
- Принадлежащий `indexerd` `llama-server` выгружается по простою, а не висит в
  памяти (~460 МиБ) вечно.
- Выбор результата поиска открывает именно выбранный чанк: несколько чанков
  одного файла (например, страницы PDF) получают отдельные ключи, поэтому 2-й или
  3-й результат больше не открывает первый чанк.
- `feagle pick --query` использует работающий `queryd` (и его тёплые эмбеддинги),
  а не всегда ищет внутри процесса.
- Операция `embed` в `queryd` принимает опциональные заголовки документов, а не
  затирает их в `null`.
- `feagle setup` по умолчанию подставляет выбранную в конфиге модель и больше не
  обещает функции «в следующей фазе».
- `feagle doctor` определяет батарею по файлу `type` в `/sys`, поэтому работает и
  с батареями `CMB0`, `BATT`, `macsmc-battery`.

## 0.1.0 (по-русски)

Первый релиз: локальный, полностью офлайновый семантический и полнотекстовый
поиск по файлам для Arch Linux на Wayland.

### Добавлено

- Разрешение XDG-путей (`paths.py`) и логирование в stderr (`log.py`) для journald.
- Конфиг TOML со значениями по умолчанию, слиянием, валидацией и
  закомментированным шаблоном (`config.py`).
- Хранилище SQLite: схема, прагмы WAL, миграции и соединения только на чтение
  (`db.py`).
- Индексатор: минимальный матчер `.gitignore`, обход файловой системы с фильтрами
  и лимитами, извлечение текста/кода с детектом бинарности, символьный чанкер с
  позициями (страница/строка), инкрементальный пайплайн с хешированием, удалением
  исчезнувших файлов и пересборкой.
- Полнотекстовый поиск по FTS5 с санитизацией запроса (ранжирование BM25).
- Эмбеддинги: пресеты моделей с закреплённым SHA256, task-промпты EmbeddingGemma 2,
  HTTP-клиент `/v1/embeddings` (батчи, определение размерности, MRL-усечение с
  перенормировкой), потоковая загрузка модели с проверкой и супервизор
  `llama-server` (переиспользование через файл порта, ленивый старт, выгрузка по
  простою).
- Векторное хранилище: загрузка `sqlite-vec` и таблица `chunks_vec` с проверкой
  размерности, KNN-поиск (`search/vec.py`).
- Гибридный поиск: слияние BM25 и векторов через RRF, FTS только по AND (чтобы
  одно общее слово не поднимало мусор).
- `queryd`: протокол NDJSON поверх unix-сокета (0600) с клиентом, соединения к БД
  только на чтение и выгрузкой сервера по простою.
- CLI: `index` (`--root`, `--full`, `--rebuild`, `--no-embed`, `--backfill`),
  `search` (`--mode auto|hybrid|fts`, `--limit`, `--json`, `--pick`, `--with-apps`),
  `status`, `open`, `doctor`, `setup`, `queryd`, `indexerd`, `bench`.
- PDF: извлечение через `pdftotext` с разбивкой по страницам (`\f`), таймаутами и
  понятным сообщением, если poppler не установлен.
- `feagle open <путь> [+N]` с настраиваемыми шаблонами (`openers.py`) и `--dry-run`.
- Демон индексации (`feagle indexerd`): цикл `watchfiles`, периодический пересчёт,
  пересканирование затронутых корней и паузы на батарее/при нагрузке (`power.py`).
- systemd user-юниты для индексатора и демона запросов с лимитами ресурсов и
  установщик (`units.py`).
- `feagle doctor`: проверки окружения с конкретными подсказками.
- `feagle setup`: интерактивная первичная настройка (корни, лаунчер, модель,
  загрузка, юниты, первый индекс) с `--yes` и флагами для неинтерактивного режима.
- Русский стемминг: отдельная колонка FTS со Snowball-стемами кириллицы; точная и
  стеммированная выдачи сливаются через RRF (схема v2 с автоматической миграцией).
- OCR сканированных PDF: страницы без текстового слоя рендерятся `pdftoppm` и
  распознаются `tesseract`; настраиваются языки (по умолчанию `rus+eng`), DPI,
  порог срабатывания и лимит страниц.
- Адаптеры лаунчеров (`launchers.py`) для fuzzel, wofi, rofi, tofi, walker с
  автодетектом и фолбэком в stdout; `feagle pick` показывает приложения первыми, а
  файлы из индекса — после, `search --pick --with-apps` подмешивает приложения в
  результаты поиска.
- Настройки качества: `[search].max_distance` отсекает нерелевантных векторных
  соседей, `[search].max_per_file` не даёт одному документу занять весь список.
- Устаревшие кодировки: не-UTF-8 файлы декодируются через cp1251 (кириллица) /
  cp1252 / latin-1 вместо превращения в кракозябры.
- Бенчмарки: `feagle bench` и скрипты в `bench/` на детерминированном
  синтетическом корпусе (скорость индексации, латентность запросов, память,
  размер БД, MRR@10/Recall@20); замеры — в README.
- Пресет модели `embeddinggemma-2-q4` (зеркало unsloth, SHA256 закреплён) —
  лёгкий дефолт.
- Упаковка: `PKGBUILD` + `feagle.install` для AUR и workflow GitHub Actions
  (ruff + pytest на Python 3.12/3.13/3.14).
- Примеры биндов для Hyprland (hyprlang и новый Lua-конфиг), Sway, niri, River и
  mango (TOML с 0.18).

### Изменено

- Модель по умолчанию — `embeddinggemma-2-q4`: измеренно на 27 % быстрее
  индексация и на ~65 МиБ меньше памяти, чем у `q8`, при одинаковом качестве.
- `feagle search` использует запущенный `queryd`, если он есть, иначе ищет в своём
  процессе (с деградацией до полнотекста без эмбеддингов).

### Производительность

- Результаты стемминга кэшируются: собственный CPU индексатора на
  кириллице-ёмких текстах сократился примерно в 3.9 раза (замер до/после; сам
  сервер модели по-прежнему доминирует по времени).

### Исправлено

- systemd-юниты пишутся с абсолютным путём к `feagle` — установки из venv и pipx
  теперь запускаются.
- CLI настраивает логирование: демоны пишут INFO в journald, интерактивные команды
  молчат.
- `llama-server` запускается с достаточным контекстом на слот для самого крупного
  чанка; чанк, который сервер отверг, больше не лишает векторов весь файл.
- Два демона, стартующие одновременно, синхронизируются через lock-файл и делят
  один `llama-server`, а не поднимают каждый свой.
- Юниты больше не отдают systemd общий runtime-каталог: перезапуск одного удалял
  сокет другого.
- После выгрузки или падения сервера эмбеддингов первый запрос оставался только
  полнотекстовым до истечения кулдауна; теперь сервер перезапускается в том же
  запросе (~1.6 с). Файл порта с мёртвым процессом (включая зомби) определяется
  сразу, без ожидания 15-секундного grace.
- `setup` не копирует юниты в конфиг пользователя, если их уже предоставляет
  пакет.

### Известные ограничения

- В FTS5 нет нативного русского стемминга; используется отдельная колонка со
  стемами (Snowball), а не токенизатор.
- OCR включается вручную, тяжёлый и требует `tesseract` с языковыми данными.
- `sqlite-vec` — pre-1.0: удаления из `vec0` не уменьшают файл без
  `index --rebuild`, новая векторная таблица резервирует группу на 1024 строки.
- Индексация упирается в CPU (~450 токенов/с на целевой машине).
- Публикация пакета в AUR — ручной шаг; `PKGBUILD` лежит в репозитории.
