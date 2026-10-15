# feagle: установка и настройка (по-русски)

Пошаговое руководство: от пустой системы до рабочего поиска по хоткею.
Рассчитано на Arch Linux + Wayland. Команды можно копировать как есть.

> Коротко: `feagle` индексирует ваши папки и ищет по ним по словам **и** по
> смыслу. Всё локально. Если семантику не ставить — работает быстрый
> полнотекстовый поиск. Полное описание — в [README](../README.md).

## 0. Что понадобится

| Компонент | Обязательно? | Зачем |
|---|---|---|
| Python 3.12+ | да | сам `feagle` |
| SQLite с FTS5 | да | полнотекстовый индекс (в Arch идёт с Python) |
| `llama-server` + модель | нет | семантический поиск «по смыслу» |
| `poppler` (`pdftotext`) | нет | индексация PDF |
| `tesseract` + языковые данные | нет | распознавание сканов PDF и картинок |
| `ffmpeg` + `mmproj` | нет | поиск картинок и видео по описанию (семантические эмбеддинги) |
| лаунчер (`fuzzel`/`wofi`/`rofi`/`tofi`/`walker`) | нет | попап по хоткею |

Без опциональных компонентов `feagle` не ломается: он просто работает на FTS и
подсказывает, что доустановить.

## 1. Установка

### Вариант A: из AUR (обычный путь для Arch)

```sh
yay -S feagle          # или другой AUR-хелпер (paru и т.п.)
feagle setup           # первичная настройка, см. шаг 3
```

Что важно знать:

- `python-sqlite-vec` — тоже пакет из AUR, хелпер соберёт его автоматически.
- Пакет ставит бинарь `/usr/bin/feagle` и юниты в `/usr/lib/systemd/user/`.

### Вариант B: через uv или pipx (без AUR)

```sh
uv tool install git+https://github.com/microwavedKing/feagle
# или
pipx install git+https://github.com/microwavedKing/feagle
feagle setup
```

`uv`/`pipx` кладут `feagle` в `~/.local/bin`. Проверьте, что этот каталог в
`PATH` (иначе в биндах указывайте полный путь — см. шаг 5).

### Вариант C: из исходников (для разработки)

```sh
git clone https://github.com/microwavedKing/feagle.git
cd feagle
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/feagle setup
```

Здесь `feagle` — это `.venv/bin/feagle`; в биндах используйте абсолютный путь.

## 2. Семантический поиск: `llama-server` и модель

Можно пропустить этот шаг: без него будет только полнотекстовый поиск.
Для поиска «по смыслу» нужны две вещи.

### 2.1. Собрать/поставить `llama-server`

Нужна сборка **новее 2026-10-06** (в ней появилась поддержка EmbeddingGemma 2).
Пакет `extra/llama-cpp` может быть старше — тогда берите AUR:

```sh
yay -S llama.cpp-git          # CPU-сборка (по умолчанию), тянет cmake, собирается ~15 мин
# варианты с ускорением, если есть GPU и драйверы:
#   yay -S llama.cpp-vulkan-git     # Intel/AMD через Vulkan
#   yay -S llama.cpp-cuda-git       # NVIDIA
```

Проверка:

```sh
llama-server --version        # ожидаемо: build новее ~11452
```

Если сборка старая, `feagle doctor` это заметит и подскажет, что обновить.

### 2.2. Модель

Проще всего — пусть скачает `feagle` (проверит SHA256):

```sh
feagle setup            # в интерактивном режиме согласиться на загрузку
```

Модель по умолчанию — `embeddinggemma-2-q4` (~169 МБ), она же самая лёгкая и
быстрая при том же измеренном качестве (см. README). Файл ляжет в
`~/.local/share/feagle/models/`.

Вручную (если качаете сами):

```
Файл:   embeddinggemma-2-UD-Q4_K_XL.gguf
Путь:   ~/.local/share/feagle/models/
SHA256: ea905fd08e8061db77a0031cbb5096d459d7cfd2bdd0ad2ba66e483f16719493
```

или укажите путь к своему GGUF в `[embeddings].model` (шаг 6).

## 3. Первичная настройка: `feagle setup`

```sh
feagle setup
```

Спросит по очереди (Enter — значение по умолчанию):

1. **Какие папки индексировать** — например `~/Documents ~/Downloads`.
2. **Лаунчер** — `auto` сам найдёт `fuzzel`/`wofi`/`rofi`/`tofi`/`walker`.
3. **Профиль модели** — оставьте `embeddinggemma-2-q4`.
4. **Размер вектора** (`mrl_dim`) — 768 по умолчанию; 256 почти не теряет в
   качестве и втрое уменьшает БД.
5. Скачать модель? Поставить юниты? Включить сервисы? Запустить первую
   индексацию?

Что он делает: пишет `~/.config/feagle/config.toml` (с бэкапом, если файл уже
был), качает модель, ставит юниты в `~/.config/systemd/user/`, включает
`feagle-queryd` и `feagle-indexerd`, запускает первую индексацию.

Неинтерактивно (удобно для скриптов):

```sh
feagle setup --yes --root ~/Documents --root ~/Downloads --model embeddinggemma-2-q4
```

## 4. Первый запуск и проверка

```sh
feagle doctor          # что не так в окружении и как исправить (OK/WARN/FAIL)
feagle status          # сколько файлов и чанков в индексе
feagle index           # разовая индексация (если setup её не делал)
feagle search "рецепт борща"          # гибридный поиск
feagle search "рецепт борща" --json   # машинно-читаемо
feagle pick                           # окно: приложения + файлы
```

Первый семантический запрос после долгого простоя занимает ~1.5–2 с (запуск
модели); дальше ~50 мс.

## 5. Хоткей в композиторе

Ниже — по одной строке для каждого композитора. Если `feagle` не в `PATH`
(venv/pipx), замените `feagle` на полный путь, например
`/home/имя/.local/bin/feagle`.

**mango** (`~/.config/mango/config.toml`, с 0.18):

```toml
[bind.default]
# одно окно: приложения + файлы из индекса
"SUPER+d" = "spawn_shell,feagle pick"
# семантический поиск: спросить запрос, затем показать ранжированные файлы
"SUPER+s" = "spawn_shell,wofi --dmenu -p 'feagle:' | xargs -r feagle search --pick"
```

Затем перечитайте конфиг (в mango по умолчанию это `SUPER+SHIFT+r`) или
перезапустите композитор.

**Hyprland** (релизы):

```ini
bind = SUPER, V, exec, feagle pick
```

**Hyprland** (latest git, новый Lua-конфиг):

```lua
hl.bind("SUPER + V", hl.dsp.exec_cmd("feagle pick"))
```

**Sway**:

```
bindsym $mod+v exec feagle pick
```

**niri** (spawn не использует shell — аргументы раздельно):

```kdl
binds {
    Mod+V { spawn "feagle" "pick"; }
}
```

**River**:

```sh
riverctl map normal Super V spawn 'feagle pick'
```

## 6. Настройка под себя (`~/.config/feagle/config.toml`)

Файл полностью закомментирован. Что чаще всего меняют:

```toml
[index]
roots = ["/home/имя/Documents", "/home/имя/Projects"]  # что индексировать
max_file_size_mb = 10          # крупнее — пропускаем
pause_on_battery = true        # не индексировать на батарее
pause_on_loadavg = 4.0         # пауза при высокой загрузке (0 = выкл.)
ocr = false                    # OCR сканов PDF (нужен tesseract + pdftoppm)
ocr_images = false             # OCR картинок png/jpg/webp/tiff (нужен tesseract)
ocr_langs = "rus+eng"

[embeddings]
model = "embeddinggemma-2-q4"  # пресет или абсолютный путь к .gguf
mrl_dim = 768                  # 768|512|256|128 (меньше — компактнее БД)
idle_timeout_min = 10          # через сколько выгружать модель простоя

[media]
images = false                 # поиск картинок по описанию (нужны mmproj + ffmpeg)
mmproj = "embeddinggemma-2-mmproj-q8"  # пресет или путь к mmproj .gguf
image_max_dim = 1024           # уменьшить длинную сторону перед эмбеддингом
images_per_run = 200           # лимит картинок на проход индексации (0 = без)
video = false                  # поиск видео по описанию (кадры через ffmpeg)
video_frame_interval = 10      # кадр каждые N секунд
video_max_frames = 60          # не больше кадров на одно видео

[search]
limit = 20
mode = "auto"                  # auto | hybrid | fts
max_distance = 0.8             # отсекать нерелевантные векторные совпадения
max_per_file = 3               # не больше чанков одного файла в выдаче

[launcher]
program = "auto"               # auto|fuzzel|wofi|rofi|tofi|walker|stdout

[openers]
default = "xdg-open {path}"
pdf     = "zathura --page {page} {path}"
py      = "code --goto {path}:{line}"
```

После правки корней:

```sh
systemctl --user restart feagle-indexerd     # демон подхватит новые папки
feagle index                                 # либо разово
```

## 7. Службы (демоны)

```sh
systemctl --user status feagle-queryd feagle-indexerd    # состояние
systemctl --user restart feagle-indexerd                 # перезапуск индексатора
systemctl --user disable --now feagle-indexerd feagle-queryd   # выключить
journalctl --user -u feagle-indexerd -f                  # живой лог
journalctl --user -u feagle-queryd -n 50                 # последние строки
```

`feagle-queryd` держит сокет и управляет моделью (запуск по требованию,
выгрузка по простою). `feagle-indexerd` следит за папками и обновляет индекс с
низким приоритетом (`Nice=19`, idle I/O).

## 8. Частые проблемы

| Симптом | Причина | Что делать |
|---|---|---|
| `search` находит мало или ничего | в индексе почти нет файлов | `feagle status`; добавить папки в `[index].roots` и `feagle index` |
| PDF не находятся | нет `pdftotext` | `sudo pacman -S poppler` |
| Сканы не находятся | у PDF нет текстового слоя, OCR выключен | `[index].ocr = true` + `tesseract`, `pdftoppm` и `tesseract-data-rus/eng`, затем `feagle index` |
| Текст с картинки/скриншота не находится | картинки не индексируются, OCR выключен | `[index].ocr_images = true` + `tesseract` и языковые данные, затем `feagle index` |
| Картинка не находится по описанию | `[media].images` выключен, нет `mmproj`/`ffmpeg` | `[media].images = true`, положить `mmproj` (пресет `embeddinggemma-2-mmproj-q8`), поставить `ffmpeg`, затем `feagle index` |
| Видео не находится по описанию | `[media].video` выключен, нет `mmproj`/`ffmpeg` | `[media].video = true` (+ `mmproj` и `ffmpeg`), затем `feagle index` |
| `open` не переходит к моменту видео | нет плейсхолдера `{time}` в опенере | добавить, напр. `mp4 = "mpv --start={time} {path}"` в `[openers]` |
| Все результаты в режиме `fts` | нет `llama-server` или модели | `feagle doctor`; поставить AUR `llama.cpp-git`, модель — через `feagle setup` |
| Первый запрос после простоя медленный (~1.5 с) | модель выгрузилась по простою | это нормально; уменьшить `idle_timeout_min` не нужно — выгрузка экономит память |
| Русские словоформы не совпадают | стемминг выключен | `[search].stemming = "auto"` (по умолчанию) |
| Слишком много мусорных результатов | семантика «ближайшие из плохих» | уменьшить `[search].max_distance` (например, 0.75) и `max_per_file` |
| Индексатор «ничего не делает» | пауза на батарее/загрузке | `pause_on_battery`, `pause_on_loadavg`; смотреть `journalctl --user -u feagle-indexerd` |
| Юниты не стартуют | в `ExecStart` не абсолютный путь | `feagle setup` пишет абсолютный путь; затем `systemctl --user daemon-reload` |
| `feagle pick` не показывает приложения | не найдены `.desktop` | проверить `echo $XDG_DATA_DIRS`; файлы лежат в `…/applications/*.desktop` |
| Окно лаунчера не открывается | лаунчеру нужен layer-shell | использовать `fuzzel`/`wofi`/`rofi` на wlroots-композиторе или `[launcher].program = "stdout"` |
| Индексация идёт долго | упор в CPU (~450 токенов/с) | это ожидаемо; модель `q4`, меньше папок, `CPUQuota` в юните |
| `llama-server` занимает ~460 МБ | веса модели в памяти | он выгружается после `idle_timeout_min`; уменьшить значение |

Полная самопроверка: `feagle doctor` — каждая строка с подсказкой.

## 9. Где что лежит (XDG)

| Что | Путь |
|---|---|
| Конфиг | `~/.config/feagle/config.toml` |
| База индекса | `~/.local/share/feagle/feagle.sqlite` |
| Модели | `~/.local/share/feagle/models/` |
| Логи (через journald) | `journalctl --user -u feagle-*`; лог сервера — `~/.local/state/feagle/llama.log` |
| Сокет и порт сервера | `$XDG_RUNTIME_DIR/feagle/` |

## 10. Обновление и удаление

**AUR:** `yay -Syu feagle` (обновление), `yay -Rns feagle` (удаление).

**uv/pipx:** `uv tool upgrade feagle` / `pipx upgrade feagle`,
`uv tool uninstall feagle` / `pipx uninstall feagle`.

**Полный сброс данных** (индекс, модель, конфиг, юниты):

```sh
systemctl --user disable --now feagle-indexerd feagle-queryd
rm -rf ~/.config/feagle ~/.local/share/feagle ~/.local/state/feagle
rm -f ~/.config/systemd/user/feagle-*.service
systemctl --user daemon-reload
```

**Пересобрать индекс** (например, после смены модели или `mrl_dim`):

```sh
feagle index --rebuild
```

## 11. Чек-лист «всё работает»

1. `feagle doctor` — нет `FAIL`, понятные `WARN` устранены.
2. `feagle status` — файлов и чанков больше нуля.
3. `feagle search "любое слово из ваших файлов"` — находит нужный файл.
4. `feagle search "перефразированный запрос" --json` — `"mode": "hybrid"`.
5. `feagle pick` — в окне есть приложения, а ниже — файлы.
6. Хоткей открывает то же самое окно.
7. `systemctl --user is-active feagle-queryd feagle-indexerd` — `active`.
