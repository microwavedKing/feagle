# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
