# feagle

Local semantic file search for Arch Linux + Wayland.

`feagle` indexes your personal files and lets you search them both by keyword
(full-text, SQLite FTS5/BM25) and by meaning (vector embeddings). It is invoked
from a compositor hotkey and opens the result at the right place (a PDF page, a
source line). Everything stays on your machine: no cloud, no accounts, no
telemetry.

> **Status:** early development (`0.1.0`). Full-text and semantic search work;
> PDF extraction, systemd units, launcher adapters and packaging are still to
> come. `llama-server` is optional — without it `feagle` degrades gracefully to
> full-text search.

## Requirements

- Python 3.12+
- SQLite with FTS5 (bundled with the Python standard library on Arch Linux)
- Optional, for semantic search:
  - `llama-server` (llama.cpp) built from a revision **newer than 2026-10-06**
    (EmbeddingGemma 2 support). `extra/llama-cpp` may be too old; the AUR
    `llama.cpp-git` package tracks master.
  - the `embeddinggemma-2-q8` GGUF model (see below)
- Optional: `poppler-utils` (`pdftotext`) for PDF indexing (later phase)
- Optional: a dmenu-compatible launcher (`fuzzel`, `wofi`, `rofi-wayland`, `tofi`, `walker`)

## Install

`feagle` follows XDG conventions and does not require root at runtime.

```sh
git clone https://github.com/microwavedKing/feagle.git
cd feagle
python3 -m venv .venv
.venv/bin/pip install -e .
```

## Usage

```sh
feagle index                 # scan configured roots (semantic when available)
feagle index --no-embed      # full-text indexing only
feagle index --backfill      # embed files that still lack vectors
feagle index --rebuild       # wipe and rebuild the index

feagle search "борщ recipe" # hybrid search (default from config)
feagle search "rust" --mode fts
feagle search "rust" --json

feagle status
feagle queryd                # run the query daemon in the foreground
```

Configuration lives in `$XDG_CONFIG_HOME/feagle/config.toml`
(`~/.config/feagle/config.toml`). Built-in defaults apply when the file is
absent; a commented template is written by `feagle setup` (planned for a later
phase). You can index a directory ad hoc with `feagle index --root PATH` before
any config exists.

## Semantic search

Embeddings are produced by a local `llama-server` (`llama-server --embeddings`,
OpenAI-compatible `POST /v1/embeddings`) and stored with `sqlite-vec`. Documents
and queries are embedded with the task prompts required by EmbeddingGemma 2, and
the vector may be truncated (MRL) to `[embeddings].mrl_dim` with re-normalization.

Getting the model:

1. Build/install `llama-server` (AUR, e.g. `llama.cpp-git`).
2. Download the GGUF and place it in `$XDG_DATA_HOME/feagle/models/`:

   ```
   embeddinggemma-2-Q8_0.gguf
   sha256 2188ac1deca4b77dffefd603c2776a9d76d9d74ec01841392982ebb840b09135
   ```

   or point `[embeddings].model` at an existing GGUF path.

`feagle` starts the server on demand, reuses one that is already running (via
`llama.port` in the runtime directory) and, when it owns it, stops it after
`[embeddings].idle_timeout_min` minutes without requests.

### Degradation

| Situation | Behaviour |
|---|---|
| no `llama-server` / model | full-text search works; indexing stores chunks without vectors |
| server dies mid-run | retried on the next request; errors go to the log |
| hybrid requested, embeddings down | falls back to full-text with a warning on stderr |
| `queryd` not running | the CLI opens the database read-only and searches in-process |

## Measured performance

Measured on the target laptop (Intel i7-8565U, 4 cores / 8 threads, NVMe, no
discrete GPU, CPU-only inference), 2026-10-14, with a tiny 20-document corpus:

| Metric | Result |
|---|---|
| `feagle index` (cold start + 20 embeddings + stop) | 3.65 s |
| Warm hybrid search, p50 (through `queryd`) | 56 ms (server 50–63 ms) |
| Model cold start (process → healthy) | ~1.5 s |
| Embedding dimension / model | 768 / `embeddinggemma-2-q8` (~310 MB) |
| Database size | 3.2 MB for 20 chunks (fixed overhead dominates at this size) |

These are smoke numbers, not benchmarks. A synthetic corpus and proper
p50/p95/RAM measurements arrive in a later phase.

## Known limitations

- No Russian stemming in FTS5 (the `porter` tokenizer covers English only);
  keyword search matches exact word forms, semantic search compensates for this.
- `sqlite-vec` is pre-1.0; deletions from `vec0` do not shrink the file until
  `feagle index --rebuild`.
- PDF extraction, systemd units, launcher adapters and packaging are not
  implemented yet (later phases).

## License

MIT. See [LICENSE](LICENSE). Embedding model weights are distributed under
their own license — see [NOTICE](NOTICE).
