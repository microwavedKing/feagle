# feagle

Local semantic file search for Arch Linux + Wayland.

`feagle` indexes your personal files and lets you search them both by keyword
(full-text, SQLite FTS5/BM25) and by meaning (vector embeddings). It is invoked
from a compositor hotkey and opens the result at the right place (a PDF page, a
source line). Everything stays on your machine: no cloud, no accounts, no
telemetry.

> **Status:** early development (`0.1.1`). Full-text and semantic search work,
> Russian word forms are matched through stemming, scanned PDFs and images can be
> OCR'd, images and videos can be found by a text description (opt-in multimodal
> embeddings), audio can be transcribed (opt-in, whisper.cpp), results open at a
> line/page/timestamp, `feagle pick` drives a launcher, and there are systemd user
> units, `feagle setup`, `feagle doctor`, benchmarks and packaging (`PKGBUILD` +
> CI). `llama-server` is optional — without it `feagle` degrades gracefully to
> full-text search.

## Requirements

- Python 3.12+
- SQLite with FTS5 (bundled with the Python standard library on Arch Linux)
- Optional, for semantic search:
  - `llama-server` (llama.cpp) built from a revision **newer than 2026-10-06**
    (EmbeddingGemma 2 support). `extra/llama-cpp` may be too old; the AUR
    `llama.cpp-git` package tracks master.
  - the `embeddinggemma-2-q4` GGUF model, or `embeddinggemma-2-q8` (see below)
- `poppler` (`pdftotext`) for PDF indexing
- Optional, for OCR of scanned PDFs and images: `tesseract` plus the language
  data you need (`tesseract-data-eng`, `tesseract-data-rus`, ...); scanned PDF
  pages also need `pdftoppm` from `poppler`
- Optional, for image search: the `mmproj` multimodal projector (preset
  `embeddinggemma-2-mmproj-q8`, ~530 MiB) and `ffmpeg` (downscaling)
- Optional, for audio search: `whisper.cpp` (`whisper-cli`) and a GGML model
  (for example `ggml-base.bin`); `ffmpeg` converts the audio
- Optional: a dmenu-compatible launcher (`fuzzel`, `wofi`, `rofi`, `tofi`, `walker`)

## Install

`feagle` follows XDG conventions and does not require root at runtime.

### From the AUR (Arch Linux)

```sh
yay -S feagle        # or another AUR helper
feagle setup
```

Note that `python-sqlite-vec` is an AUR package too, so the helper has to build
it as well.

### With uv or pipx

```sh
uv tool install git+https://github.com/microwavedKing/feagle
# or:  pipx install git+https://github.com/microwavedKing/feagle
feagle setup
```

### From a checkout (development)

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
absent; `feagle setup` writes a fully commented template. You can index a
directory ad hoc with `feagle index --root PATH` before any config exists.

A step-by-step Russian walkthrough (install, setup, hotkeys, troubleshooting) is
in [`docs/INSTALL.ru.md`](docs/INSTALL.ru.md).

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

**Images.** With `[index].ocr_images = true`, `feagle` indexes `png`, `jpg`,
`jpeg`, `webp` and `tiff` files by OCR'ing them directly with `tesseract`. No
renderer is involved, so `tesseract` alone is enough — unlike scanned PDF pages,
which also need `pdftoppm`:

```toml
[index]
ocr_images = true
ocr_langs = "rus+eng"
```

Image OCR is off by default. Recognized text goes into the same full-text index,
so a word on a screenshot becomes searchable. On the reference laptop a
1700×2200 screenshot takes about 0.65 s (`ocr_langs = "eng"`) and a 200-dpi PDF
page about 1.25 s. Without `tesseract` (or with the flag off) image files are
skipped, not indexed as binary garbage.

**Image search (semantic).** With `[media].images = true`, `feagle` also embeds
the image itself through the multimodal projector, so a *text* query can find a
picture ("a chart with a rising line", "screenshot of a terminal"). It shares the
text vector space, so it needs the `mmproj` file and `ffmpeg`:

```toml
[media]
images = true
mmproj = "embeddinggemma-2-mmproj-q8"
image_max_dim = 1024   # downscale the longest side before embedding
images_per_run = 200   # cap per indexing pass (0 = unlimited)
```

Image embeddings are **expensive** on CPU: a full-size page takes tens of
seconds, so images are downscaled first (a 384 px image encodes in about a
second) and only a limited number are done per pass. It also makes the shared
`llama-server` heavier (about 1 GiB of RAM). For text *inside* images, OCR
(`ocr_images`) is far cheaper and gives exact word matches — enable both to get
both.

**Server split.** The projector is only needed while *indexing* images/video, so
`feagle` runs a **separate media server** (`--mmproj`, its own `llama-media.*`
coordination files) that the indexer owns and unloads when idle. `queryd` keeps a
light **text-only** server (~0.5 GiB) for search, because text queries never need
the projector — the image vectors are already in the database.

**Video (semantic).** With `[media].video = true`, `feagle` extracts frames with
`ffmpeg` (one every `video_frame_interval` seconds, up to `video_max_frames`) and
embeds them like images, so a text query finds the right *moment*. Each result
carries a timestamp, and `feagle open clip.mp4 +30` treats `+30` as seconds:

```toml
[media]
video = true
video_frame_interval = 10
video_max_frames = 60
video_ocr = true        # also OCR each frame (needs tesseract)

[openers]
mp4 = "mpv --start={time} {path}"
```

Video is the heaviest media: it is `video_max_frames` image embeddings per file,
so keep the frame interval and cap modest on CPU. With `video_ocr = true` each
frame is also run through `tesseract`, so text *shown* in the video becomes
searchable (with its timestamp) in addition to the semantic frame embeddings —
at the cost of one OCR pass per frame.

**Audio (transcription).** With `[media].audio = true`, `feagle` transcribes audio
with `whisper.cpp` (`whisper-cli`) — the audio is converted to 16 kHz mono WAV
with `ffmpeg` first — and indexes the transcript as normal text, so spoken words
are searchable (full-text and semantic) and every segment keeps its time:

```toml
[media]
audio = true
whisper_binary = "whisper-cli"     # name on PATH, or an absolute path
whisper_model = "ggml-base.bin"    # a filename in the models dir, or absolute
audio_language = "auto"            # or "en" / "ru"

[openers]
mp3 = "mpv --start={time} {path}"
```

Audio needs only `whisper.cpp` and `ffmpeg` — **not** the `mmproj` file — so it
works even without the multimodal projector, and full-text search works without
`llama-server`. `feagle open talk.mp3 +90` treats `+90` as seconds.

## Launcher and hotkeys

`feagle pick` opens one launcher window containing **applications first and then
indexed files**: type to filter, and Enter launches the application or opens the
file at its position.

```sh
feagle pick                    # applications + indexed files
feagle pick --query "tarts"    # rank the files semantically (applications on top)
feagle pick --files 2000       # how many indexed files to list (default 500)
feagle pick --no-apps          # files only
```

`feagle search --pick` shows search results (and with `--with-apps` the
applications above them).

Note how launchers work: `wofi`/`rofi` filter a list **we hand them beforehand** —
they do not tell us the query while you type. So in `feagle pick` the files are
matched by the launcher itself (keyword/fuzzy over path and snippet); semantic
ranking needs the query as an argument, which is what `--query` and
`feagle search --pick` are for. Applications always come first, so they win when
both match.

The launcher is auto-detected (`fuzzel`, `wofi`, `rofi`, `tofi`, `walker`) or
forced with `[launcher].program`. With none installed it prints a numbered list
and reads a number, so it also works from scripts.

### Quickshell launcher (example)

For a live, semantic popup, `quickshell/` contains an example QML launcher for
[Quickshell](https://quickshell.org) 0.3+. It talks to `queryd` directly and is
two-phase: an `fts` request on every keystroke (instant) and a `hybrid` request
after a short debounce, plus a `warmup` when it opens so `llama-server` loads in
the background. See [`quickshell/README.md`](quickshell/README.md).

Bind it in your compositor:

**Hyprland** (released versions):

```ini
bind = SUPER, V, exec, feagle pick
```

**Hyprland** (the latest git uses the new Lua-based config):

```lua
hl.bind("SUPER + V", hl.dsp.exec_cmd("feagle pick"))
```

**Sway**:

```
bindsym $mod+v exec feagle pick
```

**niri** (spawn does not use a shell; each argument is separate):

```kdl
binds {
    Mod+V { spawn "feagle" "pick"; }
}
```

**River** (classic, `riverctl`):

```sh
riverctl map normal Super V spawn 'feagle pick'
```

**mango** (mangowm, a dwl/wlroots-based compositor). Since 0.18 the config is
TOML at `~/.config/mango/config.toml`:

```toml
[bind.default]
# one window: applications first, then indexed files
"SUPER+d" = "spawn_shell,feagle pick"
# semantic search: ask for the query, then show the ranked files
"SUPER+s" = "spawn_shell,wofi --dmenu -p 'feagle:' | xargs -r feagle search --pick"
```

`spawn_shell` runs the command through a shell; `spawn` is also available if you
prefer to hand over the arguments separately. Older mango (< 0.18) used
`~/.config/mango/config.conf`:

```ini
bind=SUPER,d,spawn,feagle pick
```

mango is wlroots-based, so layer-shell launchers such as `wofi` and `fuzzel`
work with `feagle pick` without any extra setup.

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

- OCR is opt-in and slow: about a second per PDF page and ~0.65 s per image on
  the reference laptop. It needs `tesseract` plus language data; scanned PDF
  pages also need `pdftoppm` from `poppler`.
- Image search is opt-in and CPU-heavy: it needs the `mmproj` file and `ffmpeg`,
  images are downscaled before embedding, and the embeddings server then uses
  about 1 GiB of RAM.
- Video search is the heaviest media: it embeds one frame per
  `video_frame_interval` seconds (up to `video_max_frames`), so a long clip takes
  a while to index on CPU.
- Audio is transcribed with `whisper.cpp` and indexed as text; a base model runs
  several times faster than real time on the reference laptop, but long files
  still take a while. Audio does not need the `mmproj` file.
- `sqlite-vec` is pre-1.0; deletions from `vec0` do not shrink the file until
  `feagle index --rebuild`.
- Upgrading from an older index version migrates the schema automatically on the
  next open; the full-text index is rebuilt from stored chunks (files are not
  re-read).
- The AUR package is prepared (`PKGBUILD`, `.SRCINFO`); publishing it is a manual
  step.

## License

MIT. See [LICENSE](LICENSE). Embedding model weights are distributed under
their own license — see [NOTICE](NOTICE).

---

# feagle (по-русски)

Локальный семантический поиск по файлам для Arch Linux + Wayland.

`feagle` индексирует ваши личные файлы и ищет по ним как по ключевым словам
(полнотекстово, SQLite FTS5/BM25), так и по смыслу (векторные эмбеддинги).
Вызывается хоткеем из композитора и открывает результат в нужном месте
(страница PDF, строка кода). Всё остаётся на вашей машине: без облака, аккаунтов
и телеметрии.

> **Статус:** ранняя разработка (`0.1.1`). Работают полнотекстовый и семантический
> поиск, русские словоформы (стемминг), OCR сканированных PDF и картинок, поиск
> картинок и видео по текстовому описанию (опциональные мультимодальные
> эмбеддинги), транскрипция аудио (опционально, whisper.cpp), открытие на
> строке/странице/таймкоде, `feagle pick` в лаунчере, есть systemd-юниты,
> `feagle setup`, `feagle doctor`, бенчмарки и упаковка (`PKGBUILD` + CI).
> `llama-server` — опционален: без него `feagle` деградирует до полнотекстового
> поиска.

Пошаговое руководство (установка, настройка, хоткеи, решение проблем) —
[`docs/INSTALL.ru.md`](docs/INSTALL.ru.md).

## Требования

- Python 3.12+
- SQLite с FTS5 (в Arch Linux идёт со стандартной библиотекой Python)
- Для семантического поиска (опционально):
  - `llama-server` (llama.cpp) сборки **новее 2026-10-06** (поддержка
    EmbeddingGemma 2). `extra/llama-cpp` может быть старой; AUR-пакет
    `llama.cpp-git` следит за master.
  - модель GGUF `embeddinggemma-2-q4` (по умолчанию) либо `embeddinggemma-2-q8`
- `poppler` (`pdftotext`) — для индексации PDF
- Для OCR сканированных PDF и картинок (опционально): `tesseract` и нужные языковые
  данные (`tesseract-data-eng`, `tesseract-data-rus`, …); страницам PDF нужен ещё
  `pdftoppm` из `poppler`
- Для поиска картинок (опционально): мультимодальный проектор `mmproj` (пресет
  `embeddinggemma-2-mmproj-q8`, ~530 МиБ) и `ffmpeg` (уменьшение картинок)
- Для поиска аудио (опционально): `whisper.cpp` (`whisper-cli`) и GGML-модель
  (например `ggml-base.bin`); `ffmpeg` конвертирует аудио
- Опционально: dmenu-совместимый лаунчер (`fuzzel`, `wofi`, `rofi`, `tofi`, `walker`)

## Установка

`feagle` следует XDG-конвенциям и не требует root в рантайме.

### Из AUR (Arch Linux)

```sh
yay -S feagle        # или другой AUR-хелпер
feagle setup
```

Учтите: `python-sqlite-vec` — тоже AUR-пакет, хелпер соберёт и его.

### Через uv или pipx

```sh
uv tool install git+https://github.com/microwavedKing/feagle
# или:  pipx install git+https://github.com/microwavedKing/feagle
feagle setup
```

### Из репозитория (разработка)

```sh
git clone https://github.com/microwavedKing/feagle.git
cd feagle
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/feagle setup
```

`feagle setup` — интерактивная первичная настройка: пишет конфиг, скачивает
модель (с проверкой SHA256), ставит и включает systemd-юниты, запускает первую
индексацию. Неинтерактивный вариант:

```sh
feagle setup --yes --root ~/Documents --model embeddinggemma-2-q4
```

## Использование

```sh
feagle setup                 # первичная настройка (интерактивно)
feagle doctor                # проверка окружения с подсказками, что исправить

feagle index                 # обход корней (семантика — если доступна)
feagle index --no-embed      # только полнотекстовая индексация
feagle index --backfill      # доэмбеддить файлы, у которых нет векторов
feagle index --rebuild       # стереть индекс и пересобрать

feagle search "борщ рецепт"  # гибридный поиск (режим из конфига)
feagle search "rust" --mode fts
feagle search "rust" --json
feagle search --pick         # выбрать результат в лаунчере и открыть

feagle open ~/docs/report.pdf +3   # открыть PDF на странице 3
feagle open ~/src/app.py +42       # открыть код на строке 42

feagle status
feagle queryd                # демон запросов на переднем плане
feagle indexerd              # демон индексации на переднем плане
feagle pick                  # окно лаунчера: приложения + файлы
```

Конфиг: `$XDG_CONFIG_HOME/feagle/config.toml` (`~/.config/feagle/config.toml`).
Если файла нет — работают встроенные значения по умолчанию; `feagle setup`
записывает полностью закомментированный шаблон. Можно проиндексировать папку
разово: `feagle index --root ПУТЬ`.

## Семантический поиск

Эмбеддинги считает локальный `llama-server` (`llama-server --embeddings`,
OpenAI-совместимый `POST /v1/embeddings`), векторы хранит `sqlite-vec`.
Документы и запросы эмбеддятся с task-префиксами, которых требует
EmbeddingGemma 2; вектор можно усечь (MRL) до `[embeddings].mrl_dim` с
перенормировкой.

Модель: соберите/поставьте `llama-server` (AUR, например `llama.cpp-git`) и
положите GGUF в `$XDG_DATA_HOME/feagle/models/`:

```
embeddinggemma-2-UD-Q4_K_XL.gguf
sha256 ea905fd08e8061db77a0031cbb5096d459d7cfd2bdd0ad2ba66e483f16719493
```

либо укажите путь к своему GGUF в `[embeddings].model`. По умолчанию —
лёгкий `embeddinggemma-2-q4`; доступен и `embeddinggemma-2-q8` (официальный
репозиторий ggml-org, ~310 МБ) — см. замеры ниже.

`feagle` запускает сервер по требованию, переиспользует уже запущенный (через
файл `llama.port` в runtime-каталоге) и, если владелец — он, выгружает его
после `[embeddings].idle_timeout_min` минут без запросов.

### Деградация

| Ситуация | Поведение |
|---|---|
| нет `llama-server` / модели | полнотекстовый поиск работает; индексация сохраняет чанки без векторов |
| сервер упал в рантайме | перезапускается при следующем запросе; ошибки — в лог |
| гибрид запрошен, эмбеддинги недоступны | откат к полнотексту с предупреждением в stderr |
| `queryd` не запущен | CLI открывает БД только на чтение и ищет в своём процессе |

## Открытие результатов

`feagle open <путь> [+N]` (или `--line`/`--page`) запускает программу из
`[openers]` по расширению файла, иначе — `default`. Плейсхолдеры: `{path}`,
`{line}`, `{page}`; если позиция не задана, плейсхолдер и предшествующий ему
флаг отбрасываются, поэтому один шаблон работает и с позицией, и без.

```toml
[openers]
default = "xdg-open {path}"
pdf     = "zathura --page {page} {path}"
py      = "code --goto {path}:{line}"
md      = "nvim +{line} {path}"
```

Команда разбирается через `shlex` и запускается без shell. `feagle open
--dry-run` печатает команду — удобно проверять шаблон.

## Службы (systemd user-юниты)

`feagle setup` ставит `feagle-indexerd.service` и `feagle-queryd.service` в
`$XDG_CONFIG_HOME/systemd/user/` и включает их. Вручную:

```sh
systemctl --user daemon-reload
systemctl --user enable --now feagle-indexerd feagle-queryd
```

Индексатор следит за корнями (inotify через `watchfiles`; фолбэк — опрос,
`feagle indexerd --poll`), периодически пересканирует и ставится на паузу на
батарее или при высокой загрузке. Лимиты — в юните индексатора: `Nice=19`,
`IOSchedulingClass=idle` и закомментированный `CPUQuota=200%` (200% = два ядра).
Эти директивы требуют cgroup v2 — `feagle doctor` это проверяет.

## Диагностика

```sh
feagle doctor            # python, sqlite/vec, индекс, юниты, сервер, модель, лаунчер, ...
feagle doctor --json
```

Каждая строка — `OK` / `WARN` / `FAIL` с конкретной подсказкой (какой пакет
поставить или какую команду выполнить).

## Русский текст и OCR

**Стемминг.** В FTS5 нет русского стеммера, поэтому `feagle` держит
дополнительную колонку со Snowball-стемами кириллических слов. Запрос `книги`
найдёт документ со словами `книга`, `книге`, `книгами`. Точное совпадение по
колонке `body` сохраняется, поэтому идентификаторы в коде и английские слова не
затронуты; две выдачи сливаются через RRF. Стемминг включён по умолчанию
(`[search].stemming = "auto"`), отключается значением `"none"`.

**Кодировки.** Текстовые файлы читаются как UTF-8. Если файл не является
валидным UTF-8, включается фолбэк: cp1251 (только если результат действительно
кириллический — чтобы не портить западноевропейские файлы), затем cp1252/latin-1.
Это покрывает старые русские `.txt`; koi8-r не определяется (его нельзя надёжно
отличить от cp1251) — такие файлы декодируются как cp1251.

**OCR.** У сканированных PDF нет текстового слоя. С включённым OCR страницы,
где текста почти нет, рендерятся в картинку (`pdftoppm` из poppler) и отдаются
`tesseract`:

```toml
[index]
ocr = true
ocr_langs = "rus+eng"   # для смешанных документов оставляйте английский
ocr_dpi = 300
ocr_min_chars = 20      # страницы, где текста меньше — распознаются
ocr_max_pages = 200     # предохранитель: OCR медленный
```

OCR по умолчанию выключен (тяжёлый: примерно секунда и больше на страницу).
Если нет `tesseract` или языковых данных — страницы просто останутся пустыми,
остальной индекс не пострадает, а `feagle doctor` скажет, что поставить. Для
чисто латинских документов `ocr_langs = "eng"` даёт более чистый результат, чем
`rus+eng`.

**Картинки.** При `[index].ocr_images = true` `feagle` индексирует `png`, `jpg`,
`jpeg`, `webp` и `tiff`, распознавая их напрямую через `tesseract`. Рендер не
нужен, поэтому достаточно одного `tesseract` — в отличие от страниц сканированных
PDF, которым ещё нужен `pdftoppm`:

```toml
[index]
ocr_images = true
ocr_langs = "rus+eng"
```

OCR картинок по умолчанию выключен. Распознанный текст попадает в тот же
полнотекстовый индекс, так что слово со скриншота становится находимым. На
эталонном ноутбуке скриншот 1700×2200 распознаётся примерно за 0.65 с
(`ocr_langs = "eng"`), а страница PDF при 200 dpi — примерно за 1.25 с. Без
`tesseract` (или при выключенном флаге) картинки пропускаются, а не индексируются
как бинарный мусор.

**Поиск картинок (семантический).** При `[media].images = true` `feagle` ещё и
эмбеддит саму картинку через мультимодальный проектор, поэтому *текстовый* запрос
находит изображение («график с растущей линией», «скриншот терминала»). Это то же
векторное пространство, что и у текста, но нужны файл `mmproj` и `ffmpeg`:

```toml
[media]
images = true
mmproj = "embeddinggemma-2-mmproj-q8"
image_max_dim = 1024   # уменьшить длинную сторону перед эмбеддингом
images_per_run = 200   # лимит на проход индексации (0 = без лимита)
```

Эмбеддинги картинок на CPU **дорогие**: страница в полном размере считается
десятки секунд, поэтому картинки сначала уменьшаются (384 px — примерно секунда),
а за проход обрабатывается лишь ограниченное число. Общий `llama-server` при этом
тяжелее (около 1 ГиБ RAM). Для текста *на* картинке OCR (`ocr_images`) намного
дешевле и даёт точные совпадения слов — включайте оба флага, чтобы получить и то,
и другое.

**Разделение серверов.** Проектор нужен только во время *индексации*
картинок/видео, поэтому `feagle` поднимает **отдельный медиа-сервер** (`--mmproj`,
свои файлы координации `llama-media.*`), которым владеет индексатор и выгружает по
простою. `queryd` держит лёгкий **текстовый** сервер (~0.5 ГиБ) для поиска:
текстовым запросам проектор не нужен — векторы картинок уже лежат в БД.

**Видео (семантический поиск).** При `[media].video = true` `feagle` извлекает
кадры через `ffmpeg` (по одному каждые `video_frame_interval` секунд, не более
`video_max_frames`) и эмбеддит их как картинки, поэтому текстовый запрос находит
нужный *момент*. У результата есть таймкод, а `feagle open clip.mp4 +30` трактует
`+30` как секунды:

```toml
[media]
video = true
video_frame_interval = 10
video_max_frames = 60
video_ocr = true        # ещё и OCR каждого кадра (нужен tesseract)

[openers]
mp4 = "mpv --start={time} {path}"
```

Видео — самый тяжёлый медиа-формат: это `video_max_frames` эмбеддингов картинок на
файл, поэтому на CPU держите интервал и лимит скромными. При `video_ocr = true`
каждый кадр ещё и прогоняется через `tesseract`, поэтому текст, *показанный* в
видео, становится находимым (со своим таймкодом) вдобавок к семантике кадров — ценой
одного прохода OCR на кадр.

**Аудио (транскрипция).** При `[media].audio = true` `feagle` транскрибирует аудио
через `whisper.cpp` (`whisper-cli`) — аудио сначала конвертируется в 16 кГц моно WAV
через `ffmpeg` — и индексирует транскрипт как обычный текст, поэтому произнесённые
слова ищутся (полнотекстово и семантически), а у каждого сегмента есть таймкод:

```toml
[media]
audio = true
whisper_binary = "whisper-cli"     # имя в PATH или абсолютный путь
whisper_model = "ggml-base.bin"    # имя в каталоге моделей или абсолютный путь
audio_language = "auto"            # либо "en" / "ru"

[openers]
mp3 = "mpv --start={time} {path}"
```

Аудио требует только `whisper.cpp` и `ffmpeg` — **без** `mmproj` — поэтому работает
даже без мультимодального проектора, а полнотекстовый поиск работает и без
`llama-server`. `feagle open talk.mp3 +90` трактует `+90` как секунды.

## Лаунчер и хоткеи

`feagle pick` открывает одно окно лаунчера, в котором **сначала приложения, затем
файлы из индекса**: печатаете — фильтруется, Enter запускает приложение или
открывает файл на нужной позиции.

```sh
feagle pick                    # приложения + файлы из индекса
feagle pick --query "борщ"     # ранжировать файлы по смыслу (приложения сверху)
feagle pick --files 2000       # сколько файлов показывать (по умолчанию 500)
feagle pick --no-apps          # только файлы
```

`feagle search --pick` показывает результаты поиска (а с `--with-apps` —
приложения над ними).

Важно про лаунчеры: `wofi`/`rofi` фильтруют список, который **мы им заранее
отдали** — они не сообщают нам запрос по мере ввода. Поэтому в `feagle pick`
файлы фильтрует сам лаунчер (подстрока/фаззи по пути и сниппету), а для
семантического ранжирования запрос нужно передать аргументом: `--query` или
`feagle search --pick`. Приложения всегда идут первыми, поэтому при совпадении
выигрывают они.

Лаунчер определяется автоматически (`fuzzel`, `wofi`, `rofi`, `tofi`, `walker`)
или задаётся в `[launcher].program`. Если ничего нет — печатается нумерованный
список и читается номер, так что работает и из скриптов.

### Лаунчер на Quickshell (пример)

Для живого семантического попапа в `quickshell/` лежит пример QML-лаунчера для
[Quickshell](https://quickshell.org) 0.3+. Он общается с `queryd` напрямую и
двухфазный: запрос `fts` на каждое нажатие (мгновенно) и `hybrid` после короткого
debounce, плюс `warmup` при открытии, чтобы `llama-server` прогревался в фоне. См.
[`quickshell/README.md`](quickshell/README.md).

Бинды в композиторе:

**Hyprland** (релизы):

```ini
bind = SUPER, V, exec, feagle pick
```

**Hyprland** (latest git — новый Lua-конфиг):

```lua
hl.bind("SUPER + V", hl.dsp.exec_cmd("feagle pick"))
```

**Sway**:

```
bindsym $mod+v exec feagle pick
```

**niri** (spawn не использует shell; аргументы раздельно):

```kdl
binds {
    Mod+V { spawn "feagle" "pick"; }
}
```

**River** (классический, `riverctl`):

```sh
riverctl map normal Super V spawn 'feagle pick'
```

**mango** (mangowm, на базе dwl/wlroots). С версии 0.18 конфиг — TOML,
`~/.config/mango/config.toml`:

```toml
[bind.default]
# одно окно: приложения + файлы из индекса
"SUPER+d" = "spawn_shell,feagle pick"
# семантический поиск: спросить запрос, затем показать ранжированные файлы
"SUPER+s" = "spawn_shell,wofi --dmenu -p 'feagle:' | xargs -r feagle search --pick"
```

`spawn_shell` выполняет команду через shell; есть и `spawn`, если предпочитаете
передавать аргументы раздельно. Старый mango (< 0.18) использовал
`~/.config/mango/config.conf`:

```ini
bind=SUPER,d,spawn,feagle pick
```

mango основан на wlroots, поэтому лаунчеры на layer-shell (`wofi`, `fuzzel`)
работают с `feagle pick` без дополнительной настройки.

Если `feagle` не в `PATH` сессии (установка в venv или pipx) — укажите в бинде
абсолютный путь. Синтаксис сверен с документацией композиторов 2026-10-14; mango
перешёл на TOML в 0.18, а формат конфига Hyprland меняется — сверяйтесь с доками
своей версии.

## Качество поиска

Две настройки не дают выдаче превратиться в «всё, что нашлось в векторах»:

```toml
[search]
# Игнорировать векторных соседей дальше этого (L2-расстояние на нормированных
# векторах). Неподходящий запрос тогда даёт мало результатов или ноль. 0 — выкл.
max_distance = 0.8
# Не больше стольких чанков одного файла, чтобы один документ не занял весь
# список. 0 — без ограничения.
max_per_file = 3
```

Семантика сама по себе почти всегда что-то возвращает — ближайшие чанки найдутся
даже для запроса, которого нет в тексте. `max_distance` отделяет «похожее» от
«ближайшего из плохих»: увеличьте, если отсекаются хорошие совпадения,
уменьшите для строгости.

## Измеренная производительность

Замеры на этой машине: Intel i7-8565U (4 ядра / 8 потоков), **16 ГБ RAM**, NVMe,
без дискретной видеокарты, вывод только на CPU. Целевая машина проекта слабее
(4 ядра U-серии, 8 ГБ RAM), так что эти цифры — верхняя оценка для такого класса.
Сырые результаты — в `bench/results/`. Повторить:

```sh
feagle bench --files 60 --seed 1 --out bench/results/bench.json
python bench/quality_eval.py --files 60 --seed 1
```

### Модель: по умолчанию q4

| | `embeddinggemma-2-q4` (по умолчанию) | `embeddinggemma-2-q8` |
|---|---|---|
| Размер файла | 169 МБ | 310 МБ |
| Индексация | **2.30 чанка/с** | 1.81 чанка/с |
| RSS `llama-server` | **458 МиБ** | 523 МиБ |
| Холодный старт | 1.55 с | 1.59 с |
| MRR@10 (hybrid) | 0.750 | 0.750 |
| MRR@10 (англ. / рус.) | 0.667 / 0.833 | 0.667 / 0.833 |
| Recall@20 (hybrid) | 0.750 | 0.750 |
| p50 гибридного запроса | **52 мс** | 62 мс |

Лёгкий квант примерно на 27 % быстрее индексирует и на ~65 МиБ меньше, при
**одинаковом измеренном качестве** — поэтому он дефолт; `q8` (официальный
репозиторий ggml-org) доступен через `[embeddings].model`. То же соотношение
подтвердилось на реальных книгах (4 книги, 230 чанков, MRR 0.8 у обоих, q4
индексирует на 30 % быстрее). Полнотекстовые метрики не зависят от модели:
MRR@10 0.375, Recall@20 0.500 — точного совпадения мало на перефразированных
запросах, для этого и нужна семантика.

### Латентность, память, размер

| Метрика | Результат |
|---|---|
| Гибридный запрос, p50 / p95 | 52 мс / 63 мс (эмбеддинг 50 мс, поиск 2 мс) |
| Полнотекстовый запрос, p50 / p95 | 1.4 мс / 3.8 мс |
| Скорость индексации | 2.3 чанка/с (~1.7 файла/с) — упор в CPU на обработке промпта |
| RSS `llama-server` (загружен) | ~458 МиБ, выгружается после 10 мин простоя |
| Процесс `feagle` при индексации | +6 МиБ пик |
| Холодный старт (запуск → healthy) | 1.6 с |
| База данных | ~5.6 КБ/чанк, когда векторная «страница» заполнена; на маленьких индексах доминирует предвыделение vec0 в 3.1 МиБ (см. ниже) |

Латентность запроса определяется эмбеддингом запроса (~50 мс); сам поиск
(KNN + FTS + RRF) — ~2 мс. Модель выгружается после `idle_timeout_min`, поэтому
простаивающая машина не тратит ни CPU, ни память под модель.

### О размере БД

`sqlite-vec` выделяет векторы фиксированными «группами» по 1024 строки (в этой
версии `chunk_size` не настраивается), поэтому новая таблица `chunks_vec`
резервирует ~3.1 МиБ (1024 × 768 × 4 байта) независимо от числа строк. Поэтому
бенчмарк на 65 чанках показывает ~50 КБ/чанк; в установившемся режиме — ~5.6 КБ
на чанк (3 КБ вектор + текст и FTS).

### Профилирование и одна измеренная оптимизация

`cProfile` на индексации и поиске (с заглушкой вместо сервера, чтобы мерить
только наш код) показал, что индексацию доминирует **стемминг**: 5840 вызовов
`snowballstemmer` на 130 чанков, 0.45 с из 0.56 с. Теперь результаты стемминга
кэшируются (`functools.lru_cache`), что сократило наш CPU на том же корпусе до
0.14 с (**в 3.9 раза быстрее**, `stem_text` — в 7.8 раза, реальных вызовов
стеммера 285). Поиск (~1.8 мс) упирается в сам SQLite — там ничего не менялось.

## Известные ограничения

- В FTS5 нет нативного русского стемминга; `feagle` компенсирует отдельной
  колонкой со стемами (Snowball), а не токенизатором.
- OCR включается вручную, тяжёлый (примерно секунда на страницу PDF и ~0.65 с на
  картинку на эталонном ноутбуке) и требует `tesseract` с языковыми данными; для
  страниц сканированных PDF нужен ещё `pdftoppm` из poppler.
- Поиск картинок включается вручную и тяжёл на CPU: нужны файл `mmproj` и `ffmpeg`,
  картинки уменьшаются перед эмбеддингом, а сервер эмбеддингов начинает занимать
  около 1 ГиБ RAM.
- Поиск видео — самый тяжёлый: эмбеддится один кадр каждые `video_frame_interval`
  секунд (не более `video_max_frames`), поэтому длинный клип на CPU индексируется
  долго.
- Аудио транскрибируется через `whisper.cpp` и индексируется как текст; базовая
  модель на эталонном ноутбуке быстрее реального времени в несколько раз, но
  длинные файлы всё равно обрабатываются не мгновенно. `mmproj` для аудио не нужен.
- `sqlite-vec` — pre-1.0: удаления из `vec0` не уменьшают файл без
  `feagle index --rebuild`, а новая векторная таблица резервирует группу на 1024
  строки.
- Индексация упирается в CPU (~450 токенов/с на целевой машине); большой корпус
  в первый раз индексируется небыстро.
- AUR-пакет подготовлен (`PKGBUILD`, `.SRCINFO`), публикация — ручной шаг.

## Лицензия

MIT. См. [LICENSE](LICENSE). Веса модели распространяются по своей лицензии —
см. [NOTICE](NOTICE).
