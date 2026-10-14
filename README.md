# feagle

Local semantic file search for Arch Linux + Wayland.

`feagle` indexes your personal files and lets you search them both by keyword
(full-text, SQLite FTS5/BM25) and by meaning (vector embeddings). It is invoked
from a compositor hotkey and opens the result at the right place (a PDF page, a
source line). Everything stays on your machine: no cloud, no accounts, no
telemetry.

> **Status:** early development (`0.1.0`). Full-text and semantic search work,
> Russian word forms are matched through stemming, scanned PDFs can be OCR'd,
> results open at a line/page, and `feagle search --pick` drives a launcher.
> There are systemd user units, `feagle setup` and `feagle doctor`. Packaging is
> still to come. `llama-server` is optional — without it `feagle` degrades
> gracefully to full-text search.

## Requirements

- Python 3.12+
- SQLite with FTS5 (bundled with the Python standard library on Arch Linux)
- Optional, for semantic search:
  - `llama-server` (llama.cpp) built from a revision **newer than 2026-10-06**
    (EmbeddingGemma 2 support). `extra/llama-cpp` may be too old; the AUR
    `llama.cpp-git` package tracks master.
  - the `embeddinggemma-2-q8` GGUF model (see below)
- `poppler-utils` (`pdftotext`) for PDF indexing
- Optional, for OCR of scanned PDFs: `tesseract` plus the language data you need
  (`tesseract-data-eng`, `tesseract-data-rus`, ...)
- Optional: a dmenu-compatible launcher (`fuzzel`, `wofi`, `rofi-wayland`, `tofi`, `walker`)

## Install

`feagle` follows XDG conventions and does not require root at runtime.

```sh
git clone https://github.com/microwavedKing/feagle.git
cd feagle
python3 -m venv .venv
.venv/bin/pip install -e .
```

Then run the interactive first-time setup, which writes the config, downloads
the model (SHA256-verified), installs and enables the systemd user units, and
runs the first index:

```sh
.venv/bin/feagle setup
```

A non-interactive equivalent:

```sh
.venv/bin/feagle setup --yes --root ~/Documents --model embeddinggemma-2-q8
```

## Usage

```sh
feagle setup                 # first-run configuration (interactive)
feagle doctor                # environment check with fix hints

feagle index                 # scan configured roots (semantic when available)
feagle index --no-embed      # full-text indexing only
feagle index --backfill      # embed files that still lack vectors
feagle index --rebuild       # wipe and rebuild the index

feagle search "борщ recipe" # hybrid search (default from config)
feagle search "rust" --mode fts
feagle search "rust" --json
feagle search --pick         # choose a result in a launcher and open it

feagle open ~/docs/report.pdf +3   # open a PDF at page 3
feagle open ~/src/app.py +42       # open a source file at line 42

feagle status
feagle queryd                # run the query daemon in the foreground
feagle indexerd              # run the indexing daemon in the foreground
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

## Opening results

`feagle open <path> [+N]` (or `--line`/`--page`) runs the opener configured for
the file's extension from `[openers]`, falling back to `default`. Templates use
`{path}`, `{line}` and `{page}`; an absent position drops the placeholder and a
directly preceding flag, so one template works with or without a position.

```toml
[openers]
default = "xdg-open {path}"
pdf     = "zathura --page {page} {path}"
py      = "code --goto {path}:{line}"
md      = "nvim +{line} {path}"
```

Commands are split with `shlex` and executed without a shell. `feagle open
--dry-run` prints the command it would run, which is handy for checking a
template.

## Services (systemd user units)

`feagle setup` installs `feagle-indexerd.service` and `feagle-queryd.service`
into `$XDG_CONFIG_HOME/systemd/user/` and enables them. To do it by hand:

```sh
systemctl --user daemon-reload
systemctl --user enable --now feagle-indexerd feagle-queryd
```

The indexer watches the configured roots (inotify via `watchfiles`; polling as a
fallback with `feagle indexerd --poll`), rescans periodically, and pauses while
on battery or when the load average is too high. Resource limits live in the
indexer unit: `Nice=19`, `IOSchedulingClass=idle` and a commented
`CPUQuota=200%` (200% = two CPUs). These directives need cgroup v2, which
`feagle doctor` verifies.

## Diagnostics

```sh
feagle doctor            # python, sqlite/vec, index, units, server, model, launcher, ...
feagle doctor --json
```

Each line is `OK` / `WARN` / `FAIL` with a concrete fix hint (for example which
package to install or which command to run).

## Russian text and OCR

**Stemming.** FTS5 has no Russian stemmer, so `feagle` keeps an extra FTS column
holding Snowball stems of the Cyrillic words. A query like `книги` matches a
document that says `книга`, `книге` or `книгами`. Exact matching on the original
`body` column is kept, so code identifiers and English words are unaffected; the
two rankings are fused with RRF like everything else. Stemming is on by default
(`[search].stemming = "auto"`); set it to `"none"` to disable it.

**OCR.** Scanned PDFs have no text layer. With OCR enabled, pages that have
almost no text are rendered to an image (`pdftoppm`, from poppler) and passed to
`tesseract`:

```toml
[index]
ocr = true
ocr_langs = "rus+eng"   # keep English in the list for mixed documents
ocr_dpi = 300
ocr_min_chars = 20      # pages with less text than this are OCR'd
ocr_max_pages = 200     # safety limit; OCR is slow
```

OCR is off by default (it is CPU-heavy: roughly a second or more per page).
If `tesseract` or the requested language data is missing, pages simply stay
empty, the rest of the index is unaffected, and `feagle doctor` says what to
install. For Latin-only documents `ocr_langs = "eng"` gives cleaner results than
`rus+eng`.

## Launcher and hotkeys

`feagle search --pick` runs a search, shows the results in a dmenu-compatible
launcher and opens the chosen hit at its position. The launcher is auto-detected
(`fuzzel`, `wofi`, `rofi`, `tofi`, `walker`) or forced with `[launcher].program`.
With no launcher installed it prints a numbered list to stdout and reads a
number, so it also works from scripts.

Bind it in your compositor:

**Hyprland** (released versions):

```ini
bind = SUPER, V, exec, feagle search --pick
```

**Hyprland** (the latest git uses the new Lua-based config):

```lua
hl.bind("SUPER + V", hl.dsp.exec_cmd("feagle search --pick"))
```

**Sway**:

```
bindsym $mod+v exec feagle search --pick
```

**niri** (spawn does not use a shell; each argument is separate):

```kdl
binds {
    Mod+V { spawn "feagle" "search" "--pick"; }
}
```

**River** (classic, `riverctl`):

```sh
riverctl map normal Super V spawn 'feagle search --pick'
```

**mango** (mangowm, a dwl/wlroots-based compositor). Since 0.18 the config is
TOML at `~/.config/mango/config.toml`:

```toml
[bind.default]
"SUPER+s" = "spawn_shell,feagle search --pick"
```

`spawn_shell` runs the command through a shell; `spawn` is also available if you
prefer to hand over the arguments separately. Older mango (< 0.18) used
`~/.config/mango/config.conf`:

```ini
bind=SUPER,s,spawn,feagle search --pick
```

mango is wlroots-based, so layer-shell launchers such as `wofi` and `fuzzel`
work with `feagle search --pick` without any extra setup.

If `feagle` is not on the session's `PATH` (a venv or pipx install, for
example), use its absolute path in the bind. The syntax above was checked
against the compositors' documentation on 2026-10-14; mango moved to TOML in
0.18 and Hyprland's configuration format is changing, so check your version's
docs.

## Search quality

Two settings keep results useful instead of dumping everything the vector
index can find:

```toml
[search]
# Ignore vector neighbours farther than this (L2 distance on normalized
# vectors). Unrelated queries then return few or no results. 0 disables.
max_distance = 0.8
# At most this many chunks of the same file, so one big document cannot fill
# the whole list. 0 disables.
max_per_file = 3
```

Semantic search alone almost always returns *something* — the nearest chunks are
returned even for a query that appears nowhere. `max_distance` is the knob that
separates "related" from "nearest of a bad lot"; raise it if good matches are
being filtered out, lower it for stricter results.

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

- OCR is opt-in and slow (roughly a second or more per page) and needs
  `tesseract` plus language data.
- `sqlite-vec` is pre-1.0; deletions from `vec0` do not shrink the file until
  `feagle index --rebuild`.
- Upgrading from an older index version migrates the schema automatically on the
  next open; the full-text index is rebuilt from stored chunks (files are not
  re-read).
- Packaging (PKGBUILD/AUR, CI, a tagged release) is not implemented yet.

## License

MIT. See [LICENSE](LICENSE). Embedding model weights are distributed under
their own license — see [NOTICE](NOTICE).
