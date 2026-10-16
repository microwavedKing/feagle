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
| `scanner.py` | обход корней и фильтры | `Scanner.iter_files()` → `ScanCandidate`; скрытые, `exclude_dirs`, allowlist расширений, лимиты размера, симлинки не трогаем, `exclude_paths` (данные feagle) |
| `extract.py` | текст/код/PDF/картинки | `extract()`, `Extracted(pages, kind, encoding)`; **фолбэк кодировок**; PDF через `pdftotext`; картинки — OCR (`kind="image"`) |
| `ocr.py` | OCR страниц PDF и картинок | `ocr_pdf_page()` (pdftoppm→tesseract), `ocr_image()` (только tesseract), `ocr_pages()` с лимитами |
| `media.py` | подготовка картинок и видео | `image_dimensions()`/`downscale_image()` (ffprobe/ffmpeg), `extract_frames()` (кадр каждые N с, ≤ `video_max_frames`), `VIDEO_SUFFIXES` |
| `asr.py` | транскрипция аудио (whisper.cpp) | `transcribe()` (ffmpeg → 16 кГц WAV → `whisper-cli -oj`), `Segment(start_ms,end_ms,text)`, `AUDIO_SUFFIXES` |
| `chunker.py` | чанки ~1400 симв. | `chunk_extracted()`; прилипание к границе строки (точный `line`), перекрытие 150, cap 2200, PDF не пересекает страницу, у картинок `line`/`page` = None |
| `pipeline.py` | инкрементальная синхронизация | `index_roots()`, `index_file()`, `backfill()`, `wipe()`; stat→hash→reindex, prune, `pause_check`, эмбеддинги **вне транзакции**, частичные векторы |
| `watcher.py` | изменения ФС | обёртка `watchfiles`; источник инжектируется для тестов |
| `daemon.py` | `feagle indexerd` | первый проход, пересканирование затронутых корней, периодический rescan, паузы, очередь событий |

### Эмбеддинги (`embeddings/`)

| Файл | Ответственность | Ключевое |
|---|---|---|
| `presets.py` | пресеты моделей | `ModelSpec` с SHA256/размером; `embeddinggemma-2-q4` (дефолт), `-q8`, `-bf16`, mmproj |
| `prompts.py` | task-префиксы | `format_query/format_doc`, `TASKS` (search/code/qa) |
| `client.py` | HTTP `/v1/embeddings` | батчи, порядок по `index`, discovery dim, MRL-усечение + L2-перенормировка, `health()`; `embed_images()` (content-parts, без task-префиксов) |
| `download.py` | загрузка модели | поток, SHA256/размер, сверка пина с HF tree API |
| `supervisor.py` | жизненный цикл `llama-server` | port/pid/lock/touch-файлы, `ensure_running()`, idle-stop, обнаружение мёртвого владельца, `--mmproj` при медиа |
| `service.py` | связка «супервизор + клиент» | `EmbeddingService.start()/ensure()/reset()`, `resolve_mmproj()`, `EmbedderAdapter` (touch на каждый эмбеддинг) |

### Поиск (`search/`)

| Файл | Ответственность | Ключевое |
|---|---|---|
| `fts.py` | BM25 | `search_fts()` (колонка `body`), `search_fts_stemmed()` (колонка `stem`), санитизация, `fallback_or` |
| `vec.py` | KNN | `knn()`, `insert/delete/clear_vectors()` |
| `hybrid.py` | RRF | `rrf()`, `search_text()` (точный+стем), `search_hybrid()` (+KNN), `max_distance`, `max_per_file` |
| `queryd.py` | демон на сокете | NDJSON, `QueryEngine` (восстановление эмбеддера), `QuerydServer/Client` |

### Приложения, лаунчеры, открытие

| Файл | Ответственность | Ключевое |
|---|---|---|
| `apps.py` | XDG `.desktop` | `discover_apps()`, `parse_desktop_file()`, `launch()` (gtk-launch→gio→Exec) |
| `launchers.py` | dmenu-адаптеры | fuzzel/wofi/rofi/tofi/walker/stdout; формат `key\tdisplay`; fuzzel с `--with-nth/--accept-nth` |
| `picker.py` | общий выбор | `app_items()`, `run_pick()` (приложения → запуск, файлы → `open_result`) |
| `openers.py` | открытие на позиции | шаблоны `{path}/{line}/{page}/{time}`, `shlex` без shell, `--dry-run` |

### CLI (`cli/`)

`__main__.py` (argparse, `setup_logging` по команде) + `cmd_*.py`: `index`, `search`,
`status`, `open`, `doctor`, `setup`, `queryd`, `indexerd`, `bench`, `pick`.

### Вспомогательное

`units.py` (шаблоны юнитов + установка, `using_packaged()`), `doctor.py` (проверки),
`setup.py` (TOML-рендер, бэкап, план), `bench/` (тонкие CLI-обёртки над `feagle.bench`).

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
{"op":"ping"}                                  → {"ok":true,"version":"0.1.0"}
{"op":"search","query":"...","limit":20,"mode":"auto"}
                                               → {"ok":true,"mode":"hybrid|fts","took_ms":52,
                                                  "results":[{rank,score,path,page,line,snippet,chunk_id}]}
{"op":"embed","texts":["..."],"kind":"doc"}    → {"ok":true,"dim":768,"embeddings":[[...]]}
{"op":"status"}                                → {files, chunks, vectors, vec_coverage, ...}
{"op":false ...}                               → {"ok":false,"error":"...","hint":"..."}
```

Тред на соединение, своё read-only соединение к БД на запрос. Сервер не падает на
исключениях: ошибка возвращается в `ok:false`.

### llama-server (HTTP 127.0.0.1)

`GET /health` — готовность; `POST /v1/embeddings` — батч `{"input":[...],"model":"feagle"}`;
ответ `{"data":[{"embedding":[...],"index":i}]}` (сортируем по `index`).

Команда запуска (из `supervisor.build_llama_command`):

```
llama-server -m MODEL --embeddings --host 127.0.0.1 --port P --no-webui
             --cache-ram 0 --alias feagle -c 8192 -np 4 -b 4096 -ub 4096
             # [--mmproj MMPROJ] — только при [media].images = true
```

`indexerd` обращается к `llama-server` **напрямую** через `EmbeddingService` (HTTP), а
не через сокет `queryd`: сервер общий для обоих демонов, а метка `llama.touch`
координирует их простой.

### Файлы координации в `$XDG_RUNTIME_DIR/feagle/`

| Файл | Смысл |
|---|---|
| `llama.port` | порт работающего сервера (переиспользование) |
| `llama.pid` | pid сервера (проверка «жив ли владелец», в т.ч. зомби) |
| `llama.lock` | `flock` вокруг решения «переиспользовать или запускать» |
| `llama.touch` | **общая метка активности**: любой процесс обновляет её на каждом эмбеддинге, владелец выгружает сервер только если её давно никто не трогал |
| `queryd.sock` | сокет демона запросов (0600) |

### Конфиг (`~/.config/feagle/config.toml`)

`[index]` roots, include_ext, exclude_dirs, use_gitignore, лимиты, `rescan_interval_min`,
`pause_on_battery`, `pause_on_loadavg`, `embed_batch_size`, `follow_symlinks`,
`ocr*` (в т.ч. `ocr_images`); `[embeddings]` model, mrl_dim, server_url, idle_timeout_min, threads, extra_args;
`[media]` images, mmproj, image_max_dim, images_per_run, video, video_frame_interval, video_max_frames, audio, whisper_binary, whisper_model, audio_language;
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
тела чанков пусты (полнотекстового поиска по видео нет). Результат несёт таймкод,
`open` подставляет `{time}`.

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

## 7. Тесты

- 39 файлов в `tests/`, ~480 тестов. Запуск: `.venv/bin/pytest -q`.
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
| 25 | Видео: `[media].video`, кадры `ffmpeg` → чанк с `time_ms` (схема v3) + image-вектор; `{time}` в `[openers]`, `open +N` = секунды для видео | таймкод нужен для перехода к моменту; отдельная колонка чище, чем переиспользовать `page`/`line`; текста в видео нет, поэтому только семантика | `db` (v3), `indexer/media.extract_frames`, `pipeline`, `openers`, `cmd_open` |
| 26 | Аудио: `[media].audio`, `ffmpeg` → 16 кГц WAV → `whisper-cli -oj`; сегмент = чанк с `time_ms` и текстом, **текстовые** эмбеддинги; `whisper_binary`/`whisper_model`/`audio_language` | ASR даёт текст с таймкодами → обычный FTS + семантика (не нужен `mmproj`); полнотекстовый поиск работает без `llama-server` | `indexer/asr.py`, `pipeline`, `config`, `doctor` |

## 9. Измеренные факты (кратко; подробно — в README)

- Гибридный запрос: p50 52 мс (эмбеддинг ~50 мс, поиск ~2 мс); FTS p50 1.4 мс.
- Индексация: ~2.3 чанка/с ≈ ~450 токенов/с (упор в CPU); cold start 1.6 с.
- `llama-server` RSS: ~458 МиБ (q4) / ~523 МиБ (q8) — определяется весами модели,
  флаги `-b/-ub/-np/-t` на память почти не влияют.
- БД: ~5.6 КБ/чанк в установившемся режиме; `vec0` предвыделяет ~3.1 МиБ на 1024 строки.
- Качество (синтетика, 60 док., MRR@10): hybrid 0.750 (en 0.667 / ru 0.833), FTS 0.375.
- Реальные книги (4 файла, 230 чанков): MRR@10 0.8 у q4 и q8; q4 на 30 % быстрее.
- Стемминг: наш CPU индексации 0.557 → 0.144 с (3.9×) после кэша.
