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
  - the `embeddinggemma-2-q4` GGUF model, or `embeddinggemma-2-q8` (see below)
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
.venv/bin/feagle setup --yes --root ~/Documents --model embeddinggemma-2-q4
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
   embeddinggemma-2-UD-Q4_K_XL.gguf
   sha256 ea905fd08e8061db77a0031cbb5096d459d7cfd2bdd0ad2ba66e483f16719493
   ```

   or point `[embeddings].model` at an existing GGUF path. The default preset is
   the light `embeddinggemma-2-q4`; `embeddinggemma-2-q8` (official ggml-org
   repository, ~310 MB) is available too — see the benchmarks below.

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

**Text encodings.** Text files are decoded as UTF-8. When a file is not valid
UTF-8, `feagle` falls back to cp1251 (only if the result is actually Cyrillic,
so Western European files are not mangled) and then to cp1252/latin-1. This
covers the legacy Russian `.txt` files that are common in book corpora; koi8-r
is not detected (it cannot be told apart from cp1251 reliably) and such files
will be decoded as cp1251.

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

Measured on this development machine: Intel i7-8565U (4 cores / 8 threads),
**16 GB RAM**, NVMe, no discrete GPU, CPU-only inference. The project's design
target is a weaker 4-core U-series laptop with 8 GB RAM, so treat these numbers
as an upper bound for that class of hardware. Raw results are in
`bench/results/`. Reproduce with:

```sh
feagle bench --files 60 --seed 1 --out bench/results/bench.json
python bench/quality_eval.py --files 60 --seed 1
```

### Model: q4 is the default

| | `embeddinggemma-2-q4` (default) | `embeddinggemma-2-q8` |
|---|---|---|
| File size | 169 MB | 310 MB |
| Indexing | **2.30 chunks/s** | 1.81 chunks/s |
| `llama-server` RSS | **458 MiB** | 523 MiB |
| Cold start | 1.55 s | 1.59 s |
| MRR@10 (hybrid) | 0.750 | 0.750 |
| MRR@10 (English / Russian) | 0.667 / 0.833 | 0.667 / 0.833 |
| Recall@20 (hybrid) | 0.750 | 0.750 |
| p50 hybrid query | **52 ms** | 62 ms |

The lighter quant is ~27 % faster to index and ~65 MiB smaller with **identical
measured retrieval quality**, so it is the default; `q8` (official ggml-org
repository) stays available via `[embeddings].model`. The same ranking held on a
real-text check (4 books, 230 chunks, MRR 0.8 for both, q4 indexing 30 % faster).
Full-text metrics are model-independent: MRR@10 0.375, Recall@20 0.500 — exact
matching alone is weak on paraphrased queries, which is what the semantic side is
for.

### Latency, memory, size

| Metric | Result |
|---|---|
| Hybrid query, p50 / p95 | 52 ms / 63 ms (embedding 50 ms, search 2 ms) |
| Full-text query, p50 / p95 | 1.4 ms / 3.8 ms |
| Indexing throughput | 2.3 chunks/s (~1.7 files/s) — compute-bound on CPU prompt processing |
| `llama-server` RSS (loaded) | ~458 MiB, unloaded after 10 min idle |
| `feagle` process during indexing | +6 MiB peak |
| Cold start (spawn → healthy) | 1.6 s |
| Database | ~5.6 KB/chunk once the vector page group is full; small indexes are dominated by a 3.1 MiB vec0 preallocation (see below) |

Query latency is dominated by the query embedding (~50 ms); the search itself
(KNN + FTS + RRF) is ~2 ms. The model is unloaded after `idle_timeout_min` so an
idle machine uses no GPU/CPU and no model memory.

### Database size caveat

`sqlite-vec` allocates vectors in fixed "page groups" of 1024 rows (this version
does not expose `chunk_size`), so a fresh `chunks_vec` reserves ~3.1 MiB
(1024 × 768 × 4 bytes) regardless of how many rows exist. That is why a
65-chunk benchmark reports ~50 KB/chunk; the steady-state figure is ~5.6 KB per
chunk (3 KB vector + text and FTS).

### Profiling and one measured optimization

`cProfile` over indexing and search (with a stub embedder, so only our own code
is measured) showed **stemming** dominating indexing: 5840 `snowballstemmer`
calls for 130 chunks, 0.45 s of 0.56 s. Stemming results are now cached
(`functools.lru_cache`), which cut our indexing CPU to 0.14 s for the same
corpus (**3.9× faster**, `stem_text` 7.8×, only 285 real stemmer calls). Search
(~1.8 ms) is dominated by SQLite itself, so nothing was changed there.

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
