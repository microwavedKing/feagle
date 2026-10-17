# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

## Unreleased (по-русски)

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

### Изменено

- Индексация медиа теперь использует свой `llama-server` с `--mmproj` (файлы
  координации `llama-media.*`), которым владеет индексатор и выгружает по простою.
  `queryd` держит лёгкий текстовый сервер, поэтому включение картинок/видео больше
  не заставляет демон запросов держать проектор (~1 ГиБ) ради текстового поиска.

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
