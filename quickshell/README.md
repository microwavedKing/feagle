# feagle launcher for Quickshell

A **single-window** launcher popup for [Quickshell](https://quickshell.org): type
once and get applications first, then file hits. Keyword results appear on every
keystroke and are replaced by semantic ones a moment later — no second window and
no separate "ask for a query" step.

`FeagleSearch.qml` is the reusable part: a small backend that talks to the
`feagle queryd` daemon. `FeagleLauncher.qml` is a ready-to-run demo built on it —
**drop the backend into your own Quickshell launcher** to add feagle search to it.
The demo adds a preview pane for the selected file (text with the matched lines
highlighted, an image, or a **PDF page**), `Applications` / `Files` group labels,
clickable `All` / `Apps` / `Files` filter chips, a scrollbar with an
"*N* more below" hint, and frecency ranking for applications.

Requires [Quickshell](https://quickshell.org) 0.3+ and a running `feagle queryd`
(the `feagle-queryd.service` user unit, or `feagle queryd`).

## Files

| File | What it is |
|---|---|
| `FeagleSearch.qml` | Reusable backend (`QtObject`): socket + protocol + two-phase search. |
| `FeagleLauncher.qml` | Minimalist demo popup built on `FeagleSearch`. |
| `shell.qml` | Entry point that loads the demo (`quickshell -p .`). |

## Run the demo

```sh
quickshell -p /path/to/feagle/quickshell
```

Toggle it from a compositor hotkey:

```sh
qs ipc -p /path/to/feagle/quickshell call feagle-launcher toggle   # also: show / hide
qs ipc -p /path/to/feagle/quickshell call feagle-launcher search "rust ownership"
```

`search` opens the launcher with the query already typed — handy for a second
hotkey ("search my files") next to the plain launcher one.

### Keys

| Key | Does |
|---|---|
| `↑` `↓` (or `Ctrl+P` / `Ctrl+N`) | Move the selection |
| `PgUp` / `PgDn`, `Home` / `End` | Jump |
| `Tab` / `Shift+Tab` | Cycle the filter (all → applications → files) |
| `Enter` | Run the selection (launch the app / open the file at its position) |
| `Ctrl+Enter` | Open the **folder** that contains the file |
| `Ctrl+C` | Copy the file's path (or the app's command) to the clipboard |
| `Ctrl+U` / `Ctrl+W` / `Ctrl+A` | Clear the line / delete a word / select all |
| `Esc` | Close |

Mouse works too: click a row to open it, hover to select, wheel to scroll the
list or the preview, click a filter chip in the header.

`qs ipc` must know which instance to talk to. With `-p` (as above) pass the same
path. If you installed the demo as a **named config**
(`~/.config/quickshell/feagle/shell.qml`), use `qs ipc -c feagle call …`; if it is
your **default** config (`~/.config/quickshell/shell.qml`), plain
`qs ipc call feagle-launcher toggle` works.

Hotkey examples (adjust the selection to how you run it):

- **Hyprland**: `bind = SUPER, D, exec, qs ipc -p /path/to/feagle/quickshell call feagle-launcher toggle`
- **Sway**: `bindsym $mod+d exec qs ipc -p /path/to/feagle/quickshell call feagle-launcher toggle`
- **niri**: `Mod+D { spawn "qs" "ipc" "-p" "/path/to/feagle/quickshell" "call" "feagle-launcher" "toggle"; }`
- **River**: `riverctl map normal Super D spawn 'qs ipc -p /path/to/feagle/quickshell call feagle-launcher toggle'`

The example must be running to answer `qs ipc`; start it with your session
(`quickshell -p /path/to/feagle/quickshell -d`) or as a systemd user service.

## Add feagle to your own launcher

Copy `FeagleSearch.qml` next to your launcher's QML (then the type is available
directly) and wire four things: `active`, the query, the list, and activation.

```qml
import Quickshell

Item {
    id: myLauncher
    property bool open: false

    FeagleSearch {
        id: feagle
        active: myLauncher.open       // connect + warm up while the launcher is open
    }

    TextInput {
        id: input
        // On every keystroke: instant keyword results, semantic after a pause.
        onTextChanged: feagle.setQuery(text)
        Keys.onReturnPressed: feagle.activate(list.currentIndex)
        Keys.onEscapePressed: myLauncher.open = false
    }

    ListView {
        id: list
        model: feagle.visibleResults   // results, with the filter applied
        currentIndex: 0
        delegate: Item {
            required property var modelData
            required property int index
            // modelData.kind === "app"  → name, icon, exec
            // modelData.kind === "file" → path, snippet, line/page/time
        }
    }

    // When the launcher opens:
    //   myLauncher.open = true
    //   feagle.reset()
    //   input.forceActiveFocus()
    // When it closes, set active false (or just open = false); the socket disconnects.
}
```

The full demo (`FeagleLauncher.qml`) shows the same wiring plus icons, position
labels, hover/keyboard selection and empty states — copy whatever you like from
it.

### `FeagleSearch` reference

Configuration:

| Property | Default | Meaning |
|---|---|---|
| `socketPath` | `$XDG_RUNTIME_DIR/feagle/queryd.sock` | The queryd socket. |
| `active` | `false` | Connect and send `warmup` while true. |
| `debounceMs` | `120` | Delay before the semantic (`hybrid`) pass. |
| `appsLimit` | `8` | Applications shown above the file hits while typing. |
| `browseAppsLimit` | `300` | Applications listed when the query is empty (browse mode). |
| `limit` | `0` | Max file hits; `0` uses the daemon's configured limit. |
| `filter` | `"all"` | Client-side filter for `visibleResults`: `"all"`, `"apps"` or `"files"`. |
| `previewDebounceMs` | `80` | Delay before fetching a preview for a new selection. |
| `feagleCommand` | `$FEAGLE_BIN` or `"feagle"` | Command used to open files (a name or an absolute path). |

State (read-only for the launcher):

| Property | Meaning |
|---|---|
| `query` | Current query text. |
| `results` | Mixed list: `{kind: "app" \| "file", ...}`. |
| `visibleResults` | `results` with `filter` applied — display this one. |
| `mode` | `"fts"` (keyword) or `"hybrid"` (semantic) of the last reply. |
| `tookMs` | Duration of the last search, in milliseconds. |
| `busy` | True while a reply is pending. |
| `error` | Last daemon error, or `""`. |
| `preview` | Preview of the selected file (`{kind: "text"\|"image"\|"meta", ...}`) or `null`. |
| `previewError` | Last preview error, or `""`. |
| `connected` | Whether the socket is connected. |

Functions and signals:

| Name | Does |
|---|---|
| `setQuery(text)` | Instant `fts` request + debounced `hybrid` request. |
| `request(mode)` | Run a `launcher` request in `"fts"` or `"hybrid"`. |
| `setFilter(name)` | Set the client-side filter (`"all"` / `"apps"` / `"files"`). |
| `cycleFilter(reverse)` | Cycle the filter forward (or back); what `Tab` calls. |
| `requestPreview(item)` | Fetch a preview for `item` (files only), debounced. |
| `warmup()` | Ask the daemon to load the model in the background. |
| `reset()` | Clear the query/results and warm up again. |
| `activate(index)` | Run `visibleResults[index]`. |
| `activateItem(item)` | Launch an app or open a file for one item. |
| `activated(item)` | Emitted after `activate*` runs something. |

Launching an application also reports it to the daemon (`op: "used"`), which is
what feeds the frecency ranking below.

## Wire protocol

`FeagleSearch` is a thin client for the `queryd` socket. You can talk to it
directly from any language: **line-delimited JSON over a Unix socket** at
`$XDG_RUNTIME_DIR/feagle/queryd.sock` — one JSON object per line, one reply line
per request.

**`launcher`** — what the popup uses. Applications first, then file hits:

```json
{"op":"launcher","query":"rust ownership","mode":"fts","apps_limit":8}
```

```json
{"ok":true,"mode":"fts","took_ms":3.1,"results":[
  {"kind":"app","id":"firefox.desktop","name":"Firefox","score":50.0,"icon":"firefox","exec":["gtk-launch","firefox.desktop"]},
  {"kind":"file","rank":1,"score":0.87,"path":"/home/me/notes.md","line":12,"page":null,"time":null,"snippet":"…","chunk_id":7}
]}
```

- `mode`: `"fts"` (keyword, instant) or `"hybrid"` (semantic; needs
  `llama-server`). Omit it to use the configured default.
- `limit`: maximum file hits (optional).
- App `icon` is the `.desktop` `Icon=` name (feed it to `Quickshell.iconPath`);
  `exec` is a ready argv list — run it as-is, not through a shell.
- File `line` / `page` / `time` are `null` when not applicable; `time` is in
  **seconds**.

**`warmup`** — start loading the model; never blocks the caller:

```json
{"op":"warmup"}
{"ok":true,"warming":true}
```

`{"ok":true,"warmed":true}` means it is already loaded; `{"ok":false,...}` means
there is no embedder (keyword search still works).

**`search`** — file hits only (no applications):

```json
{"op":"search","query":"tarts","limit":20,"mode":"hybrid"}
```

**`used`** — remember that an application was launched (feeds the frecency
ranking below). Send it after running `exec`:

```json
{"op":"used","key":"firefox.desktop"}
{"ok":true}
```

**`preview`** — a small preview of one file, for a preview pane:

```json
{"op":"preview","path":"/home/me/notes.md","line":12}
{"op":"preview","path":"/home/me/book.pdf","page":7,"chunk_id":442}
```

```json
{"ok":true,"kind":"text","path":"/home/me/notes.md","size":4200,"mtime":1792195650.4,
 "line":12,"start_line":1,"total_lines":180,"truncated":false,"text":"…"}
```

- `kind` is `"text"` (context lines around `line`, at most ±12), `"image"` (the
  client loads the file itself; no bytes are sent) or `"meta"` (binary or
  unreadable: `size`, `mtime` and a `reason`).
- `line` is optional; without it the first lines come back.
- A PDF chunk has no line, so pass `page` (and `chunk_id` to anchor the window on
  the text that matched). The page is rendered with `pdftotext` and cached per
  (file, mtime, page); the reply also carries `page`. Without poppler installed
  the reply degrades to `kind:"meta"` with a reason.
- A missing file is `{"ok":false,"error":"…"}`.

**Request tags.** Any request may carry a `"req"` string; the reply echoes it, so
one connection can mix request kinds. The demo tags `"launcher"` and `"preview"`
and routes on `req`.

**`ping`** → `{"ok":true,"version":"0.2.0"}`.

**`status`** → index counts (`files`, `chunks`, `vectors`, `schema_version`, …).

**`embed`** → embeddings for arbitrary texts (used by tooling).

**Two-phase pattern.** Send `mode:"fts"` on every keystroke and `mode:"hybrid"`
after a short pause; the later reply simply replaces the list. Send `warmup`
when the popup opens so the first semantic query is not slow.

## Ranking (frecency)

The daemon remembers how often and how recently each application was launched
(`op: "used"`; stored in `$XDG_STATE_HOME/feagle/frecency.json`, where the
`feagle pick` fallback writes too). Ranking adds a **bounded** bonus — at most 5
points — on top of the match score:

- it never outranks a better match (the tiers are exact 100, prefix 50,
  substring 25, fuzzy 10, browse 1, and the bonus stays below the gaps);
- inside one tier, and for the empty-query browse list, the most-used application
  comes first.

The bonus decays with a 30-day half-life, so yesterday's application beats one
used last year. Deleting the JSON file resets the history.

## Styling and icons

`FeagleLauncher.qml` keeps all colors in a small theme block at the top (`base`,
`border`, `text`, `muted`, `selected`, `hovered`, `accent`) — edit them to match
your shell, or set `lightTheme: true` for a light palette. Quickshell exposes no
system palette, so these explicit colors are the way to theme it. Layout is just
as configurable: `cardWidth`, `cardPreviewWidth`, `cardHeight`,
`cardPreviewHeight`, `previewWidth` and `topMarginRatio`.

Icons come from the system icon theme via `Quickshell.iconPath(name, true)`:
application icons use the `icon` field, file icons are mapped from the extension.
When the theme has no such icon (the default `hicolor` theme lacks most mimetype
icons), the launcher draws a small badge with the application's initial or the
file extension instead — so nothing looks broken and there are no missing-texture
warnings. For real mimetype icons, pick a full theme with
`//@ pragma IconTheme Adwaita` at the top of your root QML (or the `QS_ICON_THEME`
variable).

## Troubleshooting

- **Empty popup / "queryd is not reachable"** — start the daemon:
  `systemctl --user start feagle-queryd` (or `feagle queryd`), then check
  `feagle doctor`.
- **Only keyword results, never "semantic"** — `llama-server` or the model is
  missing; `feagle doctor` says what to install. Keyword search still works.
- **A PDF shows "cannot read the page text"** — `pdftotext` (poppler) is missing;
  previews of other files still work.
- **Files open with the wrong command** — set `feagleCommand`, or launch the shell
  with `FEAGLE_BIN=/path/to/feagle`; a venv/pipx install is often not on the
  session `PATH`.
- **`Ctrl+C` does not copy** — the clipboard needs a running clipboard manager
  (Quickshell's `clipboardText` writes to the Wayland selection).
- **`PanelWindow is not creatable` from `qmllint`** — harmless, it is a qmllint
  limitation with Quickshell's types.
