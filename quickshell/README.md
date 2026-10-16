# feagle launcher for Quickshell (example)

A thin QML popup that talks to the `feagle queryd` daemon and shows a **mixed
list: applications first, then file hits** (with snippet and position). It is a
ready-to-copy example — adjust the styling to your shell.

Requires [Quickshell](https://quickshell.org) 0.3+ and a running `feagle-queryd`
(the `feagle-queryd.service` user unit, or `feagle queryd`).

## Run

```sh
quickshell -p /path/to/feagle/quickshell
```

Then open it from a hotkey:

```sh
qs ipc call feagle-launcher toggle    # or show / hide
```

Hotkey examples:

- **Hyprland** (`~/.config/hypr/hyprland.conf`):
  `bind = SUPER, d, exec, qs ipc call feagle-launcher toggle`
- **Sway** (`~/.config/sway/config`):
  `bindsym $mod+d exec qs ipc call feagle-launcher toggle`
- **niri** (`~/.config/niri/config.kdl`):
  `Mod+D { spawn "qs" "ipc" "call" "feagle-launcher" "toggle"; }`

## How it talks to feagle

The popup opens `$XDG_RUNTIME_DIR/feagle/queryd.sock` and sends one JSON object
per line. Two phases keep typing instant:

1. **fast** — on every keystroke: `{"op":"launcher","query":"…","mode":"fts"}`
2. **slow** — after a 120 ms pause:
   `{"op":"launcher","query":"…","mode":"hybrid"}` (semantic, replaces the list)

When the window opens it also sends `{"op":"warmup"}` so `llama-server` starts
loading in the background instead of blocking the first semantic query.

The reply is:

```json
{"ok": true, "mode": "fts", "took_ms": 3.1, "results": [
  {"kind": "app",  "id": "firefox.desktop", "name": "Firefox", "score": 50.0, "exec": ["gtk-launch", "firefox.desktop"]},
  {"kind": "file", "path": "/home/me/notes.md", "line": 12, "page": null, "time": null, "snippet": "…", "chunk_id": 7}
]}
```

Selecting an application runs its `exec`; selecting a file runs
`feagle open <path>` with `--line` / `--page` / `--time` as appropriate.

## Notes

- This is an **example**: no app icons or file previews yet, and it assumes a
  plain dark theme.
- `feagle queryd` must be reachable; if the socket is missing the popup stays
  empty (check `feagle doctor` and the `feagle-queryd` unit).
