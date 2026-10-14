# feagle

Local semantic file search for Arch Linux + Wayland.

`feagle` indexes your personal files and lets you search them both by keyword
(full-text, SQLite FTS5/BM25) and by meaning (vector embeddings). It is invoked
from a compositor hotkey and opens the result at the right place (a PDF page, a
source line). Everything stays on your machine: no cloud, no accounts, no
telemetry.

> **Status:** early development (`0.1.0`). Phase 1 provides the FTS-only core;
> semantic search arrives in a later phase. `llama-server` is optional — without
> it `feagle` degrades gracefully to full-text search.

## Requirements

- Python 3.12+
- SQLite with FTS5 (bundled with the Python standard library on Arch Linux)
- Optional: `llama-server` (llama.cpp) + a GGUF embedding model for semantic search
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
feagle index            # one-shot scan of configured roots
feagle search "query"   # full-text search (semantic when available)
feagle search "query" --json
feagle status
```

Configuration lives in `$XDG_CONFIG_HOME/feagle/config.toml`
(`~/.config/feagle/config.toml` by default) and is created with documented
defaults on first run.

## Known limitations

- No Russian stemming in FTS5 (the `porter` tokenizer covers English only);
  keyword search matches exact word forms, semantic search compensates for this.
- `sqlite-vec` is pre-1.0.
- Semantic search and PDF support are not implemented yet (later phases).

## License

MIT. See [LICENSE](LICENSE). Embedding model weights are distributed under
their own license — see [NOTICE](NOTICE).
