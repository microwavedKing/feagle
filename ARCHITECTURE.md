# feagle — архитектура и журнал решений

Документ описывает карту кода, протоколы, инварианты и **принятые решения**. Он
публикуется в репозитории; внешние факты и алгоритмы — в локальном `RESEARCH.md`.

---

## 1. Общая картина

```
            файлы пользователя (XDG-корни)
                      │  inotify/poll (watchfiles) + периодический rescan
          ┌───────────▼────────────┐
          │  feagle indexerd       │  один писатель БД
          │  scanner→extract→chunk │──────────────┐ батчи эмбеддингов
          └───────────┬────────────┘              │ (HTTP 127.0.0.1)
                      │ транзакция на файл        ▼
              ┌───────▼────────┐        ┌────────────────────┐
              │ feagle.sqlite  │        │ llama-server       │
              │ files/chunks   │◄──┐    │ --embeddings       │
              │ FTS5(+stem)    │   │    │ (--mmproj в фазе 8)│
              │ vec0           │   │    └────────▲───────────┘
              └───────┬────────┘   │ read        │ embed query
                      │ read       │             │
              ┌───────▼────────┐   │             │
  хоткей ────►│ feagle queryd  │───┘─────────────┘
  (лаунчер)   │ unix-сокет     │
              │ FTS+KNN → RRF  │
              └───────┬────────┘
                      │ NDJSON
              ┌───────▼────────┐
              │ feagle (CLI)   │ search / open / pick / status / doctor / setup / bench
              └────────────────┘
```

Ключевые идеи:
- **Один файл БД** (SQLite, WAL): метаданные, текст, FTS5 и векторы.
- **Один писатель** — `indexerd` (или разовый `feagle index`). Читатели открывают БД
  `mode=ro` + `query_only`.
- **Модель — отдельный процесс** (`llama-server`), общий для демонов, лениво
  запускается и выгружается по простою.
- **CLI — тонкая обёртка**; демон нужен только для тёплого поиска и фоновой индексации.

## 2. Карта модулей

### Ядро

| Файл | Ответственность | Ключевое |
|---|---|---|
| `paths.py` | XDG-пути (config/data/state/runtime/cache) | все пути читаются из env **при вызове** (тесты подменяют env) |
| `log.py` | логирование в stderr (journald) | `setup_logging(level)`, `get_logger(name)` |
| `config.py` | TOML: дефолты, слияние, валидация, шаблон | `DEFAULTS`, `load_config()`, `render_defaults()`; таблица `openers` — «свободная» (ключи = расширения, не предупреждать) |
| `db.py` | соединения, схема, миграции, `vec0` | `SCHEMA_VERSION=2`, `connect(read_only=)`, `ensure_vec_table(dim)`, `list_indexed_files()` |
| `power.py` | пауза индексации | батарея из `/sys/class/power_supply`, loadavg из `/proc/loadavg` |
| `stemming.py` | русские стемы для FTS | Snowball, **только кириллические токены**, `lru_cache` |
| `metrics.py` | замеры для бенчей | VmHWM/VmRSS, перцентили, pid сервера |
| `bench.py` | генератор корпуса + бенч + качество | `generate_corpus()`, `run_bench()`, `quality_report()` |

### Индексация (`indexer/`)

| Файл | Ответственность | Ключевое |
|---|---|---|
| `gitignore.py` | минимальный `.gitignore` | `GitIgnore(base, patterns)`, `matches_chain()` |
| `scanner.py` | обход корней и фильтры | `Scanner.iter_files()` → `ScanCandidate`; скрытые, `exclude_dirs`, allowlist расширений, лимиты размера, симлинки не трогаем, `exclude_paths` (данные feagle); `candidate_for(path)` — те же фильтры для одного пути (событие вотчера) |
| `extract.py` | текст/код/PDF/картинки | `extract()`, `Extracted(pages, kind, encoding)`; **фолбэк кодировок**; PDF через `pdftotext`; картинки — OCR (`kind="image"`) |
| `ocr.py` | OCR страниц PDF и картинок | `ocr_pdf_page()` (pdftoppm→tesseract), `ocr_image()` (только tesseract), `ocr_pages()` с лимитами и **пулом процессов** (`ocr_workers` ≤ `MAX_OCR_WORKERS`=2, ошибка страницы изолирована, откат в один поток), `resolve_psm()` (`ocr_psm`/`ocr_osd` → `--psm` + язык `osd`) |
| `media.py` | подготовка картинок и видео | `image_dimensions()`/`downscale_image()` (ffprobe/ffmpeg), `extract_frames()` (кадр каждые N с, ≤ `video_max_frames`), `VIDEO_SUFFIXES` |
| `asr.py` | транскрипция аудио (whisper.cpp) | `transcribe()` (ffmpeg → 16 кГц WAV → `whisper-cli -oj`), `Segment(start_ms,end_ms,text)`, `AUDIO_SUFFIXES` |
| `chunker.py` | чанки ~1400 симв. | `chunk_extracted()`; прилипание к границе строки (точный `line`), перекрытие 150, cap 2200, PDF не пересекает страницу, у картинок `line`/`page` = None |
| `pipeline.py` | инкрементальная синхронизация | `index_roots()`, `index_file()`, `index_paths()` (точечно по событиям вотчера, без обхода), `backfill()`, `wipe()`; stat→hash→reindex, prune, `pause_check`, эмбеддинги **вне транзакции**, частичные векторы |
| `watcher.py` | изменения ФС | обёртка `watchfiles`; источник инжектируется для тестов |
| `daemon.py` | `feagle indexerd` | первый проход, точечная реакция на изменения, hot-reload конфига (перезапуск вотчера по генерации), периодический rescan, паузы, очередь событий |

### Эмбеддинги (`embeddings/`)

| Файл | Ответственность | Ключевое |
|---|---|---|
| `presets.py` | пресеты моделей | `ModelSpec` с SHA256/размером; `embeddinggemma-2-q4` (дефолт), `-q8`, `-bf16`, `qwen3-embedding-0.6b-q8` (1024-dim, `pooling="last"`, `prompt_family="qwen3"`), mmproj; `MRL_DIMS` до 1024 |
| `prompts.py` | промпты по семейству модели | `PromptFamily` (`gemma`, `qwen3`), `get_family()`, `format_query/format_doc`; у Qwen3 инструкция только у запроса |
| `client.py` | HTTP `/v1/embeddings` | батчи, порядок по `index`, discovery dim, MRL-усечение + L2-перенормировка, `health()`; `embed_images()` (content-parts, без task-префиксов); `family=` выбирает промпты |
| `download.py` | загрузка модели | поток, SHA256/размер, сверка пина с HF tree API |
| `supervisor.py` | жизненный цикл `llama-server` | port/pid/lock/touch-файлы, `ensure_running()`, idle-stop, обнаружение мёртвого владельца, `--mmproj` при медиа, `--pooling` только если модель требует |
| `service.py` | связка «супервизор + клиент» | `EmbeddingService.start()/ensure()/reset()`, `resolve_mmproj()`, `EmbedderAdapter` (touch на каждый эмбеддинг); берёт `pooling`/`prompt_family` из пресета |

### Поиск (`search/`)

| Файл | Ответственность | Ключевое |
|---|---|---|
| `fts.py` | BM25 | `search_fts()` (колонка `body`), `search_fts_stemmed()` (колонка `stem`), санитизация, `fallback_or` |
| `vec.py` | KNN | `knn()`, `insert/delete/clear_vectors()` |
| `hybrid.py` | RRF | `rrf()`, `search_text()` (точный+стем), `search_hybrid()` (+KNN), `max_distance`, `max_per_file` |
| `queryd.py` | демон на сокете | NDJSON, `QueryEngine` (восстановление эмбеддера, `warmup()`, `launcher()`), `QuerydServer/Client` |

### Приложения, лаунчеры, открытие

| Файл | Ответственность | Ключевое |
|---|---|---|
| `apps.py` | XDG `.desktop` | `discover_apps()`, `parse_desktop_file()` (в т.ч. `Icon=` → `App.icon`), `launch()` (gtk-launch→gio→Exec), `match_apps()`/`score_app()` (+ опциональный `bonus`) для лаунчера |
| `frecency.py` | история запусков | `FrecencyStore` (JSON в `$XDG_STATE_HOME/feagle/`), скор = count × полураспад 30 дней, обрезка/лимит, атомарная запись |
| `launchers.py` | dmenu-адаптеры | fuzzel/wofi/rofi/tofi/walker/stdout; формат `key\tdisplay`; fuzzel с `--with-nth/--accept-nth` |
| `picker.py` | общий выбор | `app_items()`, `run_pick()` (приложения → запуск + запись в frecency, файлы → `open_result`) |
| `quickshell/` | попап-лаунчер (QML) | `FeagleSearch.qml` — переиспользуемый бэкенд (сокет, протокол, `fts`+`hybrid`, фильтр, превью), `FeagleLauncher.qml` — демо (панель превью, `Tab`); см. `quickshell/README.md` |
| `openers.py` | открытие на позиции | шаблоны `{path}/{line}/{page}/{time}`, `shlex` без shell, `--dry-run` |

### CLI (`cli/`)

`__main__.py` (argparse, `setup_logging` по команде) + `cmd_*.py`: `index`, `search`,
`status`, `open`, `doctor`, `setup`, `queryd`, `indexerd`, `bench`, `pick`.

### Вспомогательное

`units.py` (шаблоны юнитов + установка, `using_packaged()`), `doctor.py` (проверки),
`setup.py` (TOML-рендер, бэкап, план), `bench/` (тонкие CLI-обёртки над `feagle.bench`).
Вне пакета: `man/feagle.1` (man-страница), `completions/` (bash, zsh, fish) — оба
устанавливает `PKGBUILD`, а `tests/test_cli_docs.py` падает, если команда или опция
из `build_parser()` не описана в них.

## 3. Модель данных (SQLite, схема v3)

```sql
meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)
-- schema_version, created_at, embedding_dim, model_id

files(id, path UNIQUE, size, mtime_ns, hash, state, error, embedded, updated_at)
-- state: ok | error | unsupported ; embedded: 1 = векторы у всех чанков
-- path — realpath (дедуп между корнями)

chunks(id, file_id→files ON DELETE CASCADE, seq, page, line, time_ms, body)
-- UNIQUE(file_id, seq) ; page 1-based для PDF, line 1-based для текста,
-- time_ms — таймкод кадра видео (мс), у остальных NULL

chunks_fts USING fts5(body, stem, chunk_id UNINDEXED, tokenize='unicode61 remove_diacritics 2')
-- body — точный текст; stem — только кириллические стемы (иначе пусто)

chunks_vec USING vec0(chunk_id INTEGER PRIMARY KEY, embedding float[DIM])
-- создаётся лениво через ensure_vec_table(dim); DIM из live-запроса к серверу
```

Прагмы: `journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`,
`foreign_keys=ON`; файл `0600`, каталог `0700`.

Миграции: `migrate()` по `meta.schema_version`.
**v1 → v2** — пересоздание `chunks_fts` с колонкой `stem` и пересчёт стемов **из уже
сохранённых чанков** (файлы не перечитываются).
**v2 → v3** — `ALTER TABLE chunks ADD COLUMN time_ms` (таймкоды медиа; идемпотентно,
проверяется по `PRAGMA table_info`).

## 4. Протоколы и файлы взаимодействия

### queryd (NDJSON поверх unix-сокета, `$XDG_RUNTIME_DIR/feagle/queryd.sock`, 0600)

```json
{"op":"ping"}                                  → {"ok":true,"version":"0.3.0"}
{"op":"search","query":"...","limit":20,"mode":"auto"}
                                               → {"ok":true,"mode":"hybrid|fts","took_ms":52,
                                                  "results":[{rank,score,path,page,line,snippet,chunk_id}]}
{"op":"embed","texts":["..."],"kind":"doc"}    → {"ok":true,"dim":768,"embeddings":[[...]]}
{"op":"status"}                                → {files, chunks, vectors, vec_coverage, ...}
{"op":"warmup"}                                → {"ok":true,"warming":true}   (фон, не блокирует)
{"op":"launcher","query":"...","mode":"fts|hybrid","apps_limit":8}
                                               → {"ok":true,"mode":..,"results":[{kind:"app"|"file",...}]}
{"op":"used","key":"firefox.desktop"}          → {"ok":true}   (история запусков для frecency)
{"op":"preview","path":"...","line":12}        → {"ok":true,"kind":"text|image|meta","text":...}
{"op":false ...}                               → {"ok":false,"error":"...","hint":"..."}
```

Любой запрос может нести строку `req` — сервер вернёт её в ответе (`req`), чтобы
один сокет мог смешивать разные виды запросов (клиент-лаунчер различает так
`launcher` и `preview`).

Тред на соединение, своё read-only соединение к БД на запрос. Сервер не падает на
исключениях: ошибка возвращается в `ok:false`.

**Лимиты** (решения 46–48): строка запроса не длиннее 256 КиБ (иначе запрос
отклоняется, а не буферизуется), `query` — 4096 символов, у `embed` — не более 64
текстов по 8192 символа; соединений не больше 16 (лишнее отклоняется сразу, а не
копится тредами), а молчащий клиент закрывается через 120 с. Клиенты это переживают:
`QuerydClient` открывает соединение на запрос, а QML-`Socket` переподключается сам.

### llama-server (HTTP 127.0.0.1)

`GET /health` — готовность; `POST /v1/embeddings` — батч `{"input":[...],"model":"feagle"}`;
ответ `{"data":[{"embedding":[...],"index":i}]}` (сортируем по `index`).

Команда запуска (из `supervisor.build_llama_command`):

```
llama-server -m MODEL --embeddings --host 127.0.0.1 --port P --no-webui
             --cache-ram 0 --alias feagle -c 8192 -np 4 -b 4096 -ub 4096
             # [--mmproj MMPROJ] — только при [media].images = true
```

`indexerd` обращается к `llama-server` **напрямую** через `EmbeddingService` (HTTP),
а не через сокет `queryd`. При включённых медиа это **отдельный медиа-сервер**
(`--mmproj`, файлы `llama-media.*`), который индексатор поднимает и выгружает по
простою; `queryd` держит лёгкий текстовый сервер (текстовым запросам проектор не
нужен — векторы картинок уже в БД). Метка активности своя для каждой роли.

### Файлы координации в `$XDG_RUNTIME_DIR/feagle/`

| Файл | Смысл |
|---|---|
| `llama.port` | порт работающего сервера (переиспользование) |
| `llama.pid` | pid сервера (проверка «жив ли владелец», в т.ч. зомби) |
| `llama.lock` | `flock` вокруг решения «переиспользовать или запускать» |
| `llama.touch` | **общая метка активности**: любой процесс обновляет её на каждом эмбеддинге, владелец выгружает сервер только если её давно никто не трогал |
| `llama-media.{port,pid,lock,touch}` | те же файлы для **медиа-сервера** (`--mmproj`), отдельно от текстового: `queryd` держит лёгкий текстовый сервер, а медиа-сервер поднимает/выгружает индексатор |
| `queryd.sock` | сокет демона запросов (0600) |

### Конфиг (`~/.config/feagle/config.toml`)

`[index]` roots, include_ext, exclude_dirs, use_gitignore, лимиты, `rescan_interval_min`,
`pause_on_battery`, `pause_on_loadavg`, `embed_batch_size`, `follow_symlinks`,
`ocr*` (в т.ч. `ocr_images`); `[embeddings]` model, mrl_dim, server_url, idle_timeout_min, threads, extra_args;
`[media]` images, mmproj, image_max_dim, images_per_run, video, video_frame_interval, video_max_frames, video_ocr, audio, whisper_binary, whisper_model, audio_language;
`[search]` limit, mode, candidates, rrf_k, stemming, max_distance, max_per_file;
`[launcher]` program; `[openers]` (свободная таблица: расширение → шаблон).

## 5. Жизненные циклы

**Индексация файла** (`pipeline.index_file`):
stat → сравнение с БД (`size`,`mtime_ns`,`state=ok`) → иначе `blake2b` → хеш тот же →
только обновить stat («touch») → иначе extract → chunk → **эмбеддинги вне транзакции** →
транзакция: удалить старые чанки (+FTS, +vec), вставить новые (+FTS cо стемом, +vec) →
`embedded` = 1, если векторы есть у всех чанков. Ошибка экстрактора → `state=error`,
индексация продолжается.

**Медиа-картинки** (`[media].images`): файл = **один чанк и один вектор** (эмбеддинг
картинки через `--mmproj`, без task-префиксов); тело чанка — OCR-текст (если
`ocr_images`) или пусто. Картинка уменьшается (`ffmpeg`, `image_max_dim`), за проход
обрабатывается не более `images_per_run` (остальные добирает `backfill`). Без
`ffmpeg`/`mmproj` картинка пропускается с предупреждением.

**Видео** (`[media].video`): `ffmpeg` извлекает кадры (`video_frame_interval`,
≤ `video_max_frames`), каждый кадр = чанк с `time_ms` и вектор (image-эмбеддинг);
тело чанка — OCR-текст кадра при `[media].video_ocr` (tesseract), иначе пусто.
Результат несёт таймкод, `open` подставляет `{time}`.

**Аудио** (`[media].audio`): `ffmpeg` → 16 кГц моно WAV → `whisper-cli -oj`; каждый
сегмент = чанк с `time_ms` и телом-транскриптом, эмбеддинги **текстовые**
(`mmproj` не нужен, FTS работает даже без сервера). Без `whisper-cli`/`ffmpeg`
файл пропускается.

**Поиск**: CLI → (если запущен) `queryd`, иначе в процессе. `auto/hybrid`: эмбеддинг
запроса + FTS(body) + FTS(stem) + KNN → RRF → отсечение `max_distance` → лимит
`max_per_file` → топ-N. `fts`: FTS(body, с OR-фолбэком) + FTS(stem) → RRF.

**Деградация**: нет сервера/модели → FTS; сервер упал в рантайме → перезапуск в том же
запросе (`_revive_embedder`), при неудаче — FTS с предупреждением; нет poppler →
PDF пропускаются; нет tesseract → OCR пропускается; нет лаунчера → нумерованный stdout.

**Простой**: модель выгружается через `idle_timeout_min` (метка `llama.touch`);
первый запрос после этого поднимает её (~1.6 с).

## 6. Инварианты и границы

- `indexer/*` не знает про сокеты; `embeddings/*` не знает про БД; `search/*` не пишет
  в БД (исключение — запись векторов вызывается из индексатора через `search/vec.py`).
- Размерность вектора **никогда не хардкодится**: берётся live-запросом и хранится в
  `meta.embedding_dim`; несовпадение → `DimMismatchError` с указанием `--rebuild`.
- Запрос и документы эмбеддятся **одной моделью и одной размерностью**; MRL-усечение —
  только с L2-перенормировкой.
- FTS-запрос санитизируется (каждый терм в кавычках), иначе `OR/NEAR/*/-` ломают SQL.
- Никаких `shell=True`: `openers`, `apps`, `launchers`, `ocr`, `extract` — только argv.
- Тесты и бенчи работают только на синтетике/`tmp_path`; личные файлы не читаются.
- Один писатель БД; читатели `ro`; `busy_timeout`.

### Модель безопасности

Один пользователь, никаких привилегий, никакой сети «наружу» (кроме скачивания
модели по явной команде). Обещания:

- **Сокет `queryd`** — `$XDG_RUNTIME_DIR/feagle/` (0700), сам сокет 0600, тред на
  соединение. Тот, кто может открыть сокет, получает *только* операции протокола:
  поиск по индексу, статус и превью **файлов из индекса**.
- **`preview` — только по индексу** (решение 45): путь нормализуется
  (`abspath` + `expanduser`, как его хранит индексатор) и обязан быть строкой в
  `files`; симлинк отвергается, пока `[index].follow_symlinks = false` (дефолт).
  Объём чтения ограничен: `_PREVIEW_MAX_BYTES` (2 МиБ) и
  `_PREVIEW_PDF_TIMEOUT` (20 с на страницу PDF).
- **БД** — файл 0600, каталог 0700; читатели открывают `mode=ro` + `query_only`.
- **`llama-server`** слушает только `127.0.0.1` (порт в `llama.port`), без API-ключа:
  локальный процесс того же пользователя до него достучится. Это **осознанно** — ключ
  не защищает от такого процесса, зато ломает «одну команду»; наружу трафик не уходит.
- **`[embeddings].server_url`** — выбор владельца: указав чужой/удалённый сервер, он
  отправит туда тексты (дефолт — localhost). Ничего другого в сеть не уходит.
- **Индексатор** не ходит по симлинкам (`follow_symlinks = false`) и не выходит за
  корни; в сеть не обращается.
- **Границы:** это защита от *других пользователей* и от *случайного* клиента, а не от
  вредоносного процесса **того же** пользователя — такой процесс и так читает те же
  файлы напрямую. Песочницы (seccomp/landlock/`ProtectHome`) нет; изоляция —
  файловые права.

## 7. Тесты

- 43 файла в `tests/`, **673 теста**. Запуск: `.venv/bin/pytest -q`.
- Покрытие ~85% (`pytest-cov`, порог в CI — 84) и `mypy` по `src/` и `scripts/` —
  отдельными шагами CI.
- **Гейты `skipif`**: тесты, требующие `llama-server`/модели (`FEAGLE_RUN_MODEL_TESTS=1`),
  `pdftotext`, `tesseract`, `ocr_available()` — скипаются, поэтому CI проходит на
  чистом Ubuntu (без poppler/tesseract/llama).
- Фикстуры-бинарники закоммичены: `tests/fixtures/two_pages.pdf`, `scanned.pdf`.
- Внешние вызовы инжектируются: `subprocess.run`, `shutil.which`, `spawn`, `health`,
  `clock/sleep`, `runner` у лаунчеров — поэтому тесты быстрые и без окружения.
- **Урок:** тест не должен зависеть от установленных опциональных пакетов — иначе он
  падает и в CI, и в `makepkg check()` на машине сборки.

## 8. Журнал решений

| # | Решение | Почему | Где закреплено |
|---|---|---|---|
| 1 | Дефолтная модель — **`embeddinggemma-2-q4`** (зеркало unsloth, SHA256 закреплён) | замер: +27 % к скорости индексации, −65 МиБ, качество идентично q8 (синтетика и реальные книги) | `presets.py`, `config.DEFAULTS`, README |
| 2 | Сервер: `-c 8192 -np 4` (2048 токенов/слот) | при `-c 4096 -np 8` (512/слот) чанк в 564 токена падал → файл терял **все** векторы | `supervisor.SupervisorConfig` |
| 3 | Частичные векторы: при ошибке батча — повтор по одному чанку, проблемный пропускается, `embedded=0` для докачки | один плохой чанк не должен лишать файл векторов | `pipeline._embed` |
| 4 | Общая метка активности `llama.touch` | владелец (queryd) выгружал сервер **во время индексации** → покрытие 24 % | `supervisor.touch/stop_if_idle`, `service.EmbedderAdapter` |
| 5 | `flock` вокруг «переиспользовать/запустить» | два демона стартовали одновременно и поднимали **два** сервера | `supervisor._spawn_lock` |
| 6 | Мёртвый владелец (включая **зомби**) определяется сразу, grace 15 с только для живого | иначе первый запрос после падения ждал 15 с (и оставался FTS) | `supervisor._stale_owner/_process_alive` |
| 7 | Восстановление эмбеддера **в том же запросе** (`_revive_embedder`), кулдаун — только после неудачи | первый запрос после выгрузки модели не должен быть полнотекстовым | `queryd.QueryEngine` |
| 8 | Юниты: **без** `RuntimeDirectory`, `ExecStart` — абсолютный путь | общий `RuntimeDirectory` удалялся systemd при рестарте одного юнита → пропадал сокет другого; относительный путь не резолвится для venv/pipx | `units.py`, `systemd/*.service` |
| 9 | Уровень логов по команде: демоны INFO, CLI WARNING; `FEAGLE_LOG_LEVEL` переопределяет | INFO не был виден вообще (CLI не настраивал logging), а в интерактиве шумит | `cli/__main__._log_level` |
| 10 | FTS-колонка `stem` (схема v2), стемы только для кириллицы | нет русского стеммера в FTS5; латинские слова в стем-колонку не пишем, чтобы не дублировать лексические совпадения | `db._FTS_SQL`, `stemming.py` |
| 11 | Гибрид: FTS **только AND** (`fallback_or=False`), OR остаётся для чистого FTS | OR по одному общему слову («нужно») поднимал мусор на первое место | `hybrid.search_hybrid` |
| 12 | `max_distance=0.8`, `max_per_file=3` по умолчанию | семантика **всегда** что-то возвращает: `"квазарпульт"` давал 20 мусорных результатов; один файл занимал весь список | `config.DEFAULTS`, `hybrid` |
| 13 | Кодировки: cp1251 принимается только если кириллица ≥ 50 % букв; **koi8-r сознательно не угадывается** | иначе портили бы западноевропейские файлы; koi8-r нельзя надёжно отличить от cp1251 | `extract.decode_text` |
| 14 | OCR только для страниц PDF, выключен по умолчанию, лимиты страниц | OCR тяжёлый (≈сек/страница); расширение на картинки — план (HANDOFF §4.1) | `ocr.py`, `config` |
| 15 | Лаунчеры: формат `key\tdisplay`, fuzzel с `--with-nth/--accept-nth`, приложения **первыми** | fuzzel умеет отдавать только 1-ю колонку, остальные возвращают строку целиком; порядок = приоритет (лаунчеры фильтруют статический список) | `launchers.py`, `picker.py` |
| 16 | `feagle pick`: приложения парсим сами из XDG `.desktop`; запуск gtk-launch→gio→Exec; без индекса всё равно показываем приложения | пользователь просил «приложение или файл, приоритет приложению»; нужен graceful-режим | `apps.py`, `picker.py`, `cmd_pick.py` |
| 17 | Бенч: **изолированный runtime-каталог** для своего сервера | иначе замер «модели X» переиспользовал чужой сервер с моделью Y (мерили не то) | `bench.run_bench` (`runtime_dir=`) |
| 18 | Качество: ground truth по **концепту**, а не по файлу; латентность hybrid — с эмбеддингом и разбивкой | 60 документов на 12 концептов почти одинаковыми шаблонами давали неоднозначный ответ; таймер без эмбеддинга показывал фальшивые 1.9 мс | `bench.evaluate_quality/measure_queries` |
| 19 | Кэш стемминга (`lru_cache`) | профиль: 81 % нашего CPU в индексации — стемминг; 0.45 → 0.058 с | `stemming.stem_token` |
| 20 | PKGBUILD: `python-sqlite-vec` из AUR; optdepends с AUR `llama.cpp-*`; `check()` = pytest; сумма от **архива тега** | sqlite-vec нет в репах; `extra/llama-cpp` старее нужной сборки; сумма тега — курица-яйцо (пояснено комментарием) | `PKGBUILD`, `.SRCINFO` |
| 21 | `setup` не копирует юниты, если они уже стоят системно | иначе дубли в `~/.config/systemd/user` поверх пакетных | `units.using_packaged`, `cmd_setup` |
| 22 | `AGENTS.md`/`RESEARCH.md` — вне git (`.gitignore`) | внутренние документы, не для публики | `.gitignore` |
| 23 | OCR картинок за отдельным флагом `[index].ocr_images`, только `tesseract` (без `pdftoppm`) | без OCR/эмбеддингов картинки не дают текста; отдельный флаг не навязывает их тем, кому нужен только PDF-OCR, а рендерер для картинок не нужен | `config.DEFAULTS`, `pipeline._build_scanner`, `extract.extract_image` |
| 24 | Поиск картинок: `[media].images` (opt-in), «1 файл = 1 чанк/вектор», предуменьшение `ffmpeg` (≤ `image_max_dim`), лимит `images_per_run` за проход, `--mmproj` только при включённых медиа | на CPU image-эмбеддинг дорог (десятки секунд на полный размер) и ест RAM; переиспользование `chunks_vec` не требует схемы v3; для текста на картинке дешевле OCR | `config.DEFAULTS`, `pipeline`, `client.embed_images`, `supervisor`, `indexer/media.py` |
| 25 | Видео: `[media].video`, кадры `ffmpeg` → чанк с `time_ms` (схема v3) + image-вектор; `{time}` в `[openers]`, `open +N` = секунды для видео; `video_ocr` добавляет OCR кадра в тело чанка | таймкод нужен для перехода к моменту; отдельная колонка чище, чем переиспользовать `page`/`line`; OCR кадров даёт полнотекстовый поиск по тексту в видео (опционально, дороже) | `db` (v3), `indexer/media.extract_frames`, `pipeline`, `openers`, `cmd_open` |
| 26 | Аудио: `[media].audio`, `ffmpeg` → 16 кГц WAV → `whisper-cli -oj`; сегмент = чанк с `time_ms` и текстом, **текстовые** эмбеддинги; `whisper_binary`/`whisper_model`/`audio_language` | ASR даёт текст с таймкодами → обычный FTS + семантика (не нужен `mmproj`); полнотекстовый поиск работает без `llama-server` | `indexer/asr.py`, `pipeline`, `config`, `doctor` |
| 27 | Лаунчер: `queryd` отдаёт `warmup` (фон) и `launcher` (готовый список, приложения первыми); двухфазный поиск на стороне клиента (`fts` сразу, `hybrid` после debounce); кэш `.desktop` в `QueryEngine` | холодный старт `llama-server` не должен блокировать ввод; тонкий QML — логика скоринга в Python; лаунчеры (wofi/rofi) не сообщают запрос | `search/queryd.py`, `apps.match_apps`, `quickshell/` |
| 28 | Разделение серверов: медиа-сервер (`--mmproj`, файлы `llama-media.*`) поднимает только индексатор (`EmbeddingService(media=True)`), `queryd` — текстовый (`media=False`) | текстовым запросам проектор не нужен (векторы картинок уже в БД), а держать `--mmproj` (~1 ГиБ) ради текстового поиска нельзя; медиа-сервер выгружается по простою | `supervisor.SupervisorConfig.media`, `service.media_enabled`, `cmd_index/indexerd/setup` |
| 29 | Медиа-модели ставит `setup` (`--media-images/--media-video/--media-audio` + интерактивные вопросы): включает флаги `[media]` и качает `mmproj` / whisper-модель (пресеты, SHA256) | «одна команда» для включения медиа: пользователь не должен вручную править конфиг и угадывать имена/хэши файлов; загрузка идёт тем же проверяющим кодом, что и текстовая модель | `setup.SetupPlan.media_*`, `cmd_setup._download_media_models`, `presets.WHISPER_PRESETS` |
| 30 | GPU-offload не хардкодим: `llama-server` берёт бэкенд своей сборки, флаги — через `[embeddings].extra_args` (применяются к текстовому и медиа-серверу), а `doctor` печатает `--list-devices` | железо у всех разное (CPU/Vulkan/CUDA/ROCm), а набор флагов зависит от сборки; `extra_args` уже проходит в оба сервера, отдельный код не нужен | `config` (шаблон), `doctor.check_llama_devices`, README/INSTALL |
| 31 | Лаунчер: протокол вынесен в переиспользуемый `FeagleSearch.qml` (`QtObject`), демо `FeagleLauncher.qml` — одно окно; приложения первыми + файлы, `fts` мгновенно и `hybrid` после debounce; `icon` приложения в ответе `launcher` | «два окна» (`wofi \| feagle search --pick`) — плохой UX; один живой попап на Quickshell решает и это, а компонент позволяет встроить feagle в свой QS-лаунчер без копирования логики | `quickshell/FeagleSearch.qml`, `quickshell/FeagleLauncher.qml`, `queryd.launcher` (`icon`), `apps.App.icon` |
| 32 | Frecency считается **на сервере** и добавляется как **ограниченный** бонус (≤ 5 к базовому скору, с полураспадом 30 дней); хранилище — JSON в `$XDG_STATE_HOME/feagle/frecency.json`, `feagle pick` пишет туда же | скоринг приложений уже живёт в `queryd`, поэтому историю видит любой клиент (включая dmenu-фолбэк), а не только QML; бонус меньше зазора между тирами (1/10/25/50/100), поэтому история не может вытеснить лучшее совпадение, но упорядочивает внутри тира и в режиме обзора | `frecency.py`, `queryd._app_bonus`/`used`, `apps.match_apps(bonus=…)`, `picker.run_pick` |
| 33 | Превью — операция `preview` в `queryd` (текст вокруг строки ±12, маркер картинки, метаданные) + панель справа в демо; фильтр результатов (`all/apps/files`) — **на клиенте** (`Tab`), без нового запроса | превью — это чтение файла и логика, ей место рядом с индексом (любой клиент получит её даром); фильтр же не должен терять уже полученную семантическую выдачу, поэтому он клиентский и мгновенный | `queryd.preview`, `FeagleSearch.visibleResults/setFilter`, `FeagleLauncher` (панель, `Tab`) |
| 34 | Параллельный OCR страниц PDF: `ProcessPoolExecutor` на `ocr_workers` (по умолчанию 2), **жёсткий потолок 2**; воркер — модульная функция, возвращающая `(page, text, error)`; при недоступности пула или своём `ocr_page` — один поток | `tesseract` однопоточный, поэтому единственный способ использовать несколько ядер — процессы; страница 300 dpi после декодирования — десятки МБ, поэтому потолок 2 (цель 8 ГБ); ошибка страницы не должна ронять проход, а результат пишется по номеру страницы, чтобы порядок не зависел от воркеров | `ocr.ocr_pages`/`_ocr_page_task`/`_process_pool`, `config.ocr_workers`, `extract_pdf(ocr_workers=…)`, `pipeline` |
| 35 | Качество OCR: `ocr_psm` (`"auto"` → значение tesseract по умолчанию) и `ocr_osd` (`--psm 1` + язык `osd` в `-l`; без `osd` в списке языков OSD вообще не запускается). Текст по-прежнему берём из `txt`-вывода, а **TSV оставили только для замеров** | замер показал, что TSV-реконструкция текста нормализованно **совпадает** с `txt` на обеих фикстурах → порядок слов уже верный, переписывать рабочее нечего; а `--psm`/DPI на синтетике не меняют CER/WER (0.000), но 300 dpi втрое медленнее 150 — значит нужен замерный скрипт, а не «улучшение» вслепую | `ocr.resolve_psm`, `ocr.osd_available` (кэш), `config.ocr_psm/ocr_osd`, `doctor.check_ocr`, `bench/ocr_eval.py` |
| 36 | Изменение вотчера индексируется **точечно**: `index_paths()` обрабатывает ровно пути из события (каталог — как маленький корень), а удаления в батче снимаются сразу; полный обход корня остаётся только у периодического rescan | обход + stat всего корня на каждое сохранение файла — это O(корень) работы на O(1) изменений (замер: 185 мс против 2 мс на 3000 файлах, 86×); фильтры переиспользуются (`Scanner.candidate_for`), чтобы точечный путь не индексировал то, что пропустил бы обход; страховка от пропущенных удалений — периодический rescan | `pipeline.index_paths`/`_remove_path`/`_apply_outcome`, `Scanner.candidate_for`, `daemon.index_changes` |
| 37 | `indexerd` перечитывает конфиг между итерациями (`reload_config`); смена корней перезапускает вотчер **по поколениям** (у каждого продюсера свой stop-event; терминальный `None` кладёт только текущее поколение); `--root` отключает reload; битый конфиг → лог и прежний конфиг | правки корней/лимитов/OCR не должны требовать рестарта юнита (ручная операция + простой индексации); поколения нужны, чтобы остановка старого вотчера не завершала демон (иначе смена корней = выход из цикла) | `daemon._reload_config`/`_start_watcher`/`_restart_watcher`/`_produce`, `cmd_indexerd` |
| 38 | Вторая модель — пресет `qwen3-embedding-0.6b-q8` (1024-dim): `--pooling last`, `prompt_family="qwen3"`; промпты вынесены в `PromptFamily` (`gemma`/`qwen3`) и берутся из пресета; `MRL_DIMS` дополнен 1024 | у Qwen3 другой пулинг (в GGUF его нет) и другая схема ввода: инструкция только у запроса (`Instruct: …\nQuery: …`), у документов — ничего, а формат критичен для качества; привязка к пресету значит, что новый пресет приносит свои настройки и не требует правок в клиенте | `presets.ModelSpec(pooling, prompt_family)`, `prompts.PromptFamily`, `client(family=…)`, `service._make_client`, `supervisor.build_llama_command` |
| 39 | `[search].max_distance` стал модельно-зависимым: `ModelSpec.max_distance` применяется, пока в конфиге стоит встроенное значение (`hybrid.effective_max_distance`); явно заданное пользователем значение всегда важнее | порог подобран под EmbeddingGemma, а у Qwen3 шкала L2 сдвинута: правильные пары 0.66–1.25, «чужие» ~1.39, поэтому порог 0.8 отбрасывал **всех** соседей и гибрид молча вырождался в полнотекстовый поиск (замер: MRR@10 0.000 на синтетике и 0.077 на книгах против 0.75/1.000) | `presets.ModelSpec.max_distance`, `hybrid.effective_max_distance`, `queryd.search`, `bench.evaluate_quality` |
| 40 | Гибрид **никогда не хуже** полнотекстового поиска: при пустом результате (все векторные соседи отсечены `max_distance` и AND-ранжирование пусто) добавляется OR-ранжирование — **только** в этом случае | лаунчер рисует `fts` мгновенно, а ответ `hybrid` заменяет список: на запросах вне корпуса `hybrid` возвращал 0 там, где `fts` давал 7 (замер: «как варить борщ» — `fts=7`, `hybrid=0`), и список пустел; OR по-прежнему не подмешивается, пока у семантики есть мнение (тест «ignores single-word OR matches») | `hybrid.search_hybrid`, `FeagleSearch.resultsFor` |
| 41 | Версия — **один источник**: `src/feagle/__init__.py`; `pyproject.toml` читает её через hatchling; `scripts/release.py bump\|sum` синхронизирует `PKGBUILD`/`.SRCINFO`/примеры в доках и закрепляет сумму архива тега | версия была в 7 местах руками (легко забыть), а сумма архива — курица-яйцо; скрипт не тегирует, не коммитит и не пушит, только правит файлы и печатает команды | `pyproject.toml`, `scripts/release.py` |
| 42 | Конфиг предупреждает, когда `[search].max_distance` не сходится с измеренной шкалой пресета, а `mrl_dim` больше размерности модели; `doctor` показывает ту же нестыковку подсказкой. Правило **то же**, что у `effective_max_distance`: значение, равное встроенному дефолту, расхождением не считается | класс бага Qwen3 (порог 0.8 молча вырождал гибрид в полнотекст) не должен повторяться незаметно; иначе о нём узнаёшь только по пустой выдаче | `config._warn_model_knobs`, `doctor.check_model`, `hybrid.effective_max_distance` |
| 43 | `.html`/`.htm`/`.xml` индексируются со **снятием разметки** (`HTMLParser`; `<script>`/`<style>`/`<head>` игнорируются), `.log` — как текст; все четыре в дефолтном `include_ext` | в личных папках много `.html`/`.xml`, которые feagle молча пропускал; сырая разметка пустила бы теги в эмбеддинг и разбавила его; битая страница откатывается к сырому тексту, а не роняет проход | `extract.extract_markup`/`MARKUP_SUFFIXES`, `config.DEFAULTS` |
| 44 | `_ResilientEmbedder` пробрасывает **любой** атрибут обёрнутого эмбеддера через тот же retry (`__getattr__`), но остаётся «неспособным», если обёртка текстовая | пайплайн определяет способность к картинкам через `getattr(embedder, "embed_images", None)`, а обёртка пробрасывала только `embed_documents`/`embed_queries` → в `indexerd` **каждая** картинка индексировалась без вектора (поиск картинок не работал никогда; `feagle index` не затронут) | `daemon._ResilientEmbedder` |
| 45 | `preview` отдаёт **только** файлы из таблицы `files` (путь нормализуется как у индексатора; симлинк отвергается при `follow_symlinks = false`); модель безопасности описана в разделе §6 | `preview` читал любой переданный путь от имени пользователя: любой локальный процесс, открывший сокет 0600, мог вычитать любой файл владельца (находка аудита). Индекс — естественный allowlist: всё, что может попасть в ответ, уже прочитано индексатором, и ничего другого достижимо не становится | `queryd.QueryEngine._indexed`/`preview`, `ARCHITECTURE.md` §6 |
| 46 | `_embed` различает «сервер недоступен» (`None`) и «этот текст не эмбеддится» (список с `None`); `backfill` продолжает проход при контентной ошибке и бросает его только после **3** подряд `ServerUnavailable` | `backfill` бросал весь проход на первом же `vectors is None`: один упрямый файл (картинка, кадры видео) оставлял без векторов всю очередь. Обратная опасность — мёртвый сервер: там каждая попытка стоит клиентского таймаута, поэтому проходу нужен ограничитель, но только для этого случая | `pipeline._embed`/`backfill` (`_BACKFILL_SERVER_FAILURES`) |
| 47 | Лимиты `queryd`: 256 КиБ на строку запроса, 4096 символов на `query`, 64 текста × 8192 символа на `embed`; не больше 16 соединений одновременно (лишнее отклоняется сразу) и 120 с простоя на соединение | сокет 0600 и один пользователь — это не защита от *своего* сбойного клиента: без границ один такой клиент копил бы треды и память. Отклонять быстрее, чем блокировать accept-loop, а таймаут простоя безопасен: `QuerydClient` открывает соединение на запрос, а QML-`Socket` переподключается при обрыве (проверено по исходникам Quickshell v0.3.2, не по догадке) | `queryd._MAX_*`, `_Handler.timeout`, `_UnixServer.process_request` |
| 48 | Старт `llama-server` повторяется до 3 раз **на новом порту**, но только если процесс вышел сразу; живой, но нездоровый сервер считается ошибкой с первой попытки | `_free_port` освобождает сокет до того, как `llama-server` займёт порт: окно, в котором порт перехватывают, выглядит как мгновенный выход. Ретрай убирает этот сбой, а разделение по «вышел/жив» не даёт сломанной модели (или неверным флагам) трижды оплачивать `health_timeout` | `supervisor._start_server` (`_SPAWN_ATTEMPTS`), `_free_port` |
| 49 | man-страница и комплишены — **статические файлы** (`man/feagle.1`, `completions/`), а не генератор; расхождение с `build_parser()` ловит `tests/test_cli_docs.py`, `scripts/release.py bump` синхронизирует версию/дату в `.TH` | генератор комплишенов — это новый код на три шелла и его собственные баги; статические файлы проще читать и править, а тест по настоящему парсеру не даёт им отстать от CLI. Пакет ставит их по стандартным путям (bash-completion, zsh `site-functions`, fish `vendor_completions.d`), поэтому «установлено, но не работает» не случается | `man/feagle.1`, `completions/*`, `tests/test_cli_docs.py`, `PKGBUILD`, `scripts/release.py` |
| 50 | Пауза по нагрузке **возобновляется через 5 минут** (`PAUSED_RETRY_S`), в том числе когда периодический rescan выключен (`rescan_interval_min = 0`); порог `pause_on_loadavg = 4.0` оставлен | замер: 12 картинок 512 px подряд (изолированный медиа-сервер) — 3.7 с/картинка, loadavg 1.95 → 3.61, то есть сам эмбеддинг порог не пробивает, но любая чужая работа рядом — да. Пауза же проверяется **перед каждым файлом** и завершает проход, а повтор был привязан к интервалу rescan (по умолчанию 6 ч) и при `0` не происходил вовсе — очередь вставала | `daemon._next_rescan`/`run`, `config` (комментарий), `tests/test_daemon.py` |
| 51 | Аудио остаётся **ASR → текст → текстовые эмбеддинги**; пункт «аудио-эмбеддинги через `mmproj`» снят как невыполнимый на нашем стеке | `llama-server --help` этой сборки не имеет ни одного аудио-флага (только `--mmproj`, `--image-min-tokens`, `--mtmd-batch-max-tokens`), а проектор EmbeddingGemma — vision: аудиовход в эмбеддинги невозможен без смены модели на аудио-мультимодальную, что меняет весь пресет ради функции, которую whisper.cpp уже закрывает текстом | `ARCHITECTURE.md` §8, `plan.md` |
| 52 | `feagle pick` **остаётся** (фолбэк), депрекации нет | ТЗ (`AGENTS.md`) прямо требует адаптеры лаунчеров (fuzzel/wofi/rofi/tofi/walker/stdout) и работу без Quickshell; попап — рекомендуемый путь, но не обязательный. Убирать рабочий путь, который требуется ТЗ, нечего | README (раздел «Fallback: feagle pick»), `plan.md` |
| 53 | Размер БД считается **вместе с WAL** и после `wal_checkpoint(TRUNCATE)`; у `corpus_eval.py` появились `--max-distance` и `--db` (переиспользование БД со сверкой модели и размерности), у `ocr_eval.py` — `--osd-pdf/--osd-page` и три поворота | главный файл без WAL на маленькой базе недосчитывает почти всю свежую запись: отсюда и взялось «`mrl_dim = 256` даёт базу вдвое меньше» вместо реальных −28 % (8735 → 6274 Б/чанк). Без переиспользования БД свип порогов стоил бы полную индексацию корпуса на каждую ячейку вместо секунд | `bench._db_size/_db_size_after_checkpoint`, `bench/corpus_eval.py`, `bench/ocr_eval.py` |
| 54 | Дефолты `max_distance = 0.8` и `mrl_dim = 768` **подтверждены замером** и оставлены | на книгах (852 чанка, 13 запросов) порог 0.6 вырождает гибрид в fts (MRR 0.635 / recall 0.692), а 0.8/1.0/0 дают 1.000/1.000 — 0.8 стоит ровно на границе полезного. `mrl_dim = 256` даёт тот же MRR при базе на 28 % меньше, но усечение необратимо (обратно только переиндексацией), и четверть выигрыша этого не стоит | `config.DEFAULTS`, README «Search quality»/«Vector size», `ARCHITECTURE.md` §9 |

## 9. Измеренные факты (кратко; подробно — в README)

- Гибридный запрос: p50 52 мс (эмбеддинг ~50 мс, поиск ~2 мс); FTS p50 1.4 мс.
- Индексация: ~2.3 чанка/с ≈ ~450 токенов/с (упор в CPU); cold start 1.6 с.
- `llama-server` RSS: ~458 МиБ (q4) / ~523 МиБ (q8) — определяется весами модели,
  флаги `-b/-ub/-np/-t` на память почти не влияют.
- БД: ~8.7 КБ/чанк на 1300 чанках (1200 файлов, 768 измерений) — ~3 КБ вектор +
  текст/FTS/строки; `vec0` предвыделяет ~3.1 МиБ на 1024 строки, поэтому на 65
  чанках выходит ~54 КБ/чанк, а на 434 — 11 КБ.
- `mrl_dim`: на книгах (8 файлов, 852 чанка, 13 запросов) 768 и 256 дают одинаковые
  MRR@10 1.000, а база — **8735 против 6274 Б/чанк (−28 %)**. Прежние «8.7 против
  4.2 КБ/чанк» были ошибкой замера: считался главный файл БД без WAL, где на
  маленькой базе лежит почти вся свежая запись (исправлено в `feagle.bench`).
  На синтетике (65 чанков, 60 запросов) 256 даже выше (0.933 против 0.778), но там
  числами управляет предвыделение vec0. Дефолт оставлен 768.
- `max_distance` проверен на тех же книгах (q4, mrl 768, 852 чанка, 13 запросов):
  **0.6 → hybrid вырождается в fts** (MRR 0.635, recall@10 0.692 — все векторные
  соседи отсечены, вытягивает только OR-фолбэк, решение 40); **0.8 / 1.0 / 0
  (выключено) → MRR 1.000, recall@10 1.000**. Дефолт 0.8 остаётся: он ровно на
  границе полезного, а выше порог на этом корпусе уже ничего не меняет.
- Модели на тех же корпусах: Qwen3-Embedding-0.6B (1024) — синтетика **1.000** против
  0.778 у EmbeddingGemma; на книгах 0.712, но это прежний корпус (4 файла, 230
  чанков) — на текущем (852 чанка) прогон с дефолтным батчем 16 не даёт 100 %
  покрытия векторов (таймауты клиента), см. запись про `embed_batch_size` в README.
  FTS одинаков (0.375 / 0.635).
- `max_distance` не переносится между моделями: правильные пары у EmbeddingGemma
  лежат на L2 0.55–0.80, у Qwen3 — 0.66–1.25 («чужие» ~1.39). Порог 0.8 у Qwen3
  отбрасывал всех соседей: MRR@10 0.000 (синтетика) и 0.077 (книги) до фикса,
  1.000 и 0.712 после (книги — прежний корпус из 230 чанков).
- Качество (синтетика, 60 док., 65 чанков, MRR@10): hybrid **0.778** (en 0.722 / ru
  0.833), Recall@20 0.833, FTS 0.375. На книгах (852 чанка) hybrid 1.000 у q4 и q8.
- Реальные книги (8 файлов, 852 чанка, 13 запросов): MRR@10 **1.000** у q4 и q8;
  база 8.7 КБ/чанк (9.9 у Qwen3 — см. ниже).
- Стемминг: наш CPU индексации 0.557 → 0.144 с (3.9×) после кэша.
- OCR (синтетический скан, 6 страниц, `eng`, 300 dpi): 0.97 с/страница в один
  поток → 0.52 с/страница на двух (**1.78×**). Качество: CER/WER = 0.000 при всех
  `--psm` и 150/200/300 dpi, средняя уверенность в словах 95.9–96.3; 300 dpi ≈ втрое
  медленнее 150 dpi.
- OSD: на двухстрочной фикстуре он не разворачивает страницу (CER 0.787, «weak
  margin») — текста для уверенности не хватает, и это ничего не доказывало. На
  **текстоёмкой** странице (`think-python.pdf`, стр. 8, 478 слов, 300 dpi,
  `psm 1 + eng+osd`) поворот на 180° разворачивается: CER **0.839 → 0.020** —
  ровно как у неповёрнутой (0.020); поворот на 90° и так читается без OSD
  (CER 0.020 у `psm 3`). Цена на простое (два независимых прогона): 2.29 → 2.82 с
  на страницу для нормальной (+23 %) и 2.34 → 2.86 с для повёрнутой, поэтому
  `ocr_osd` остаётся выключенным по умолчанию. Воспроизведение:
  `python bench/ocr_eval.py --osd-pdf testdata/books/think-python.pdf --osd-page 8`.
- Вотчер: переиндексация одного изменённого файла вместо обхода корня —
  185 мс → 2 мс (**86×**) на корне с 3000 файлов.
- Эмбеддинг изображений на CPU (8 ядер, без GPU): 640×480 ≈ **11 с**; 1024 px —
  **>60 с**, то есть не влезает в клиентский таймаут (60 с) и обрывает проход;
  512 px ≈ **8.2 с**; 256 px ≈ 7.5 с (почти не быстрее — есть фиксированная цена
  ~7 с). Дефолт `image_max_dim = 1024` на CPU нерабочий, рабочая точка — 512.
  Сервер обрабатывает запросы по одному, поэтому таймаут клиента оставляет сервер
  занятым и следующие запросы встают в очередь.
- Повторный замер 512 px **в изоляции** (только медиа-сервер, изолированный
  runtime-каталог, без OCR и без чужой нагрузки): **3.7 с/картинка** в среднем
  (12 подряд, min 3.1 / max 5.3), 1-минутный loadavg **1.95 → 3.61** за ~45 с.
  Цифра 8.2 с выше — из полного прохода индексатора (оба сервера, OCR, очередь
  запросов), то есть 3.7 с — это цена самого эмбеддинга, а нагрузка на систему
  всё равно остаётся ниже порога паузы 4.0, пока рядом не работает что-то ещё.
- Качество кода: `ruff` чист (набор `E/F/W/I/UP/B/BLE/SIM/RUF/PERF/RET/TCH`),
  `mypy` чист по `src/` и `scripts/`, покрытие ~85% (порог в CI 84).
