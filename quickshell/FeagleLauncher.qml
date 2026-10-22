// feagle launcher — one window, everything: applications first, then file hits,
// ranked semantically as you type (instant keyword results, then a semantic
// refresh). No second window, no separate "ask for a query" step.
//
// The feagle protocol lives in FeagleSearch.qml; this file only draws and keys.
//
//   Run the demo:      quickshell -p /path/to/feagle/quickshell
//   Toggle from a key: qs ipc call feagle-launcher toggle
//   With a query:      qs ipc call feagle-launcher search "rust ownership"
//
// To use feagle inside your own launcher, copy FeagleSearch.qml and read its
// header — you do not need this file.
pragma ComponentBehavior: Bound

import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import Quickshell.Widgets

PanelWindow {
    id: root

    // ---- state -------------------------------------------------------------
    property bool open: false
    property int currentIndex: 0
    /// True when the daemon has not answered for a moment after opening.
    property bool daemonSlow: false
    /// A short message shown in the footer instead of the counters ("" = none).
    property string flash: ""

    // ---- theme (the default lives in gui.toml; the settings panel writes it) --
    /// Light or dark, from the popup settings ("theme"; the documented default
    /// is dark). Every colour below follows it.
    readonly property bool lightTheme: root.setting("theme", "dark") === "light"
    readonly property color base: lightTheme ? "#f7f7fb" : "#1e1e2e"
    readonly property color border: lightTheme ? "#d9d9e3" : "#313244"
    readonly property color text: lightTheme ? "#1f1f2e" : "#cdd6f4"
    readonly property color muted: lightTheme ? "#6b6b7b" : "#a6adc8"
    readonly property color selected: lightTheme ? "#e6e6f2" : "#313244"
    readonly property color hovered: lightTheme ? "#eeeef7" : "#28283a"
    readonly property color accent: lightTheme ? "#2b6cb0" : "#89b4fa"

    // ---- layout (defaults live in gui.toml; the settings panel writes them) --
    property int cardWidth: 620
    property int cardPreviewWidth: 940
    property int cardHeight: 440
    property int cardPreviewHeight: 480
    readonly property int previewWidth: root.setting("preview_width", 360)
    property real topMarginRatio: 0.15
    /// How many text columns a row draws (title, path, meta); the icon is always
    /// drawn. `compact` rows are one line tall and skip the path column.
    readonly property int rowColumns: root.setting("row_columns", 3)
    readonly property bool compactRows: root.setting("layout", "detailed") === "compact"
    /// Whether the preview pane is drawn at all.
    readonly property bool showPreview: root.setting("show_preview", true)
    /// The settings panel is open (the gear in the footer; Escape closes it).
    property bool settingsOpen: false
    onSettingsOpenChanged: {
        // The field sits under the panel: do not let typing vanish behind it.
        if (root.settingsOpen)
            field.focus = false;
        else if (root.open)
            field.forceActiveFocus();
    }
    /// What the panel lists, in display order; `feagle.guiSchema` supplies the
    /// ranges, defaults and choices. Keep in step with `gui.SETTINGS`.
    readonly property var settingsOrder: [
        "language", "show_preview", "preview_width", "row_columns", "layout",
        "theme", "default_filter", "apps_limit", "debounce_ms"
    ]
    /// UI language: "auto" follows LC_ALL/LC_MESSAGES/LANG, the panel can pin
    /// "en" or "ru".
    readonly property string language: {
        const wanted = root.setting("language", "auto");
        if (wanted === "en" || wanted === "ru")
            return wanted;
        const locale = [Quickshell.env("LC_ALL"), Quickshell.env("LC_MESSAGES"),
                        Quickshell.env("LANG")].filter((value) => value).join(" ");
        return locale.toLowerCase().indexOf("ru") === 0 ? "ru" : "en";
    }
    /// Whether paths are shown in full instead of `~/…` (Ctrl+T).
    property bool fullPaths: false
    /// Where the ↑/↓ walk stands in `feagle.history`; -1 is the live input.
    property int historyIndex: -1

    visible: open
    color: "transparent"
    exclusiveZone: 0
    anchors { top: true; bottom: true; left: true; right: true }
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.namespace: "feagle-launcher"
    WlrLayershell.keyboardFocus:
        root.open ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None

    // ---- feagle backend ----------------------------------------------------
    FeagleSearch {
        id: feagle
        active: root.open
        onResultsChanged: root.currentIndex = 0
        onVisibleResultsChanged: root.syncPreview()
    }

    Timer {
        id: connectTimer
        interval: 800
        repeat: false
        running: root.open && !feagle.connected
        onTriggered: root.daemonSlow = true
    }

    Timer {
        id: flashTimer
        interval: 2500
        repeat: false
        onTriggered: root.flash = ""
    }

    // ---- helpers -----------------------------------------------------------
    readonly property string home: Quickshell.env("HOME") || ""

    /// Russian UI strings, keyed by the English text. `t()` falls back to the
    /// key, so a string that is added here but not translated still reads as
    /// English instead of breaking.
    readonly property var ru: ({
        "Search files and applications…": "Искать файлы и приложения…",
        "Type to search files": "Введите запрос для поиска файлов",
        "Type to search your files and applications": "Начните печатать: файлы и приложения",
        "Applications": "Приложения",
        "Files": "Файлы",
        "All": "Все",
        "Apps": "Прил.",
        "semantic": "семантика",
        "keyword": "ключевые слова",
        "Copied ": "Скопировано: ",
        "Application": "Приложение",
        "No preview: ": "Без превью: ",
        "Loading preview…": "Загрузка превью…",
        "page": "стр.",
        "lines": "строки",
        "Settings": "Настройки",
        "saved on change · gui.toml": "сохраняется сразу · gui.toml",
        "Settings need the query daemon: ": "Настройки требуют демон запросов: ",
        "queryd is not reachable — start feagle-queryd":
            "queryd недоступен — запусти feagle-queryd",
        "Full paths": "Полные пути",
        "Short paths (~)": "Короткие пути (~)",
        "Pinned": "Закреплено",
        "Unpinned": "Откреплено",
        "Only files can be pinned": "Закрепить можно только файл",
        "alt+1…9  ↑↓ history  tab filter  ↵ open  ^c copy  ^d pin  ^t paths  esc close":
            "alt+1…9  ↑↓ история  tab фильтр  ↵ открыть  ^c копировать  ^d закрепить  ^t пути  esc закрыть"
    })

    /// A UI string in the current language (the English text is the key).
    function t(key) {
        if (root.language !== "ru")
            return key;
        const value = root.ru[key];
        return value !== undefined ? value : key;
    }

    /// "3 results" / "3 результата" with the right Russian plural.
    function resultsText(count) {
        if (root.language !== "ru")
            return count + (count === 1 ? " result" : " results");
        const ten = count % 10;
        const hundred = count % 100;
        if (ten === 1 && hundred !== 11)
            return count + " результат";
        if (ten >= 2 && ten <= 4 && (hundred < 12 || hundred > 14))
            return count + " результата";
        return count + " результатов";
    }

    function moreText(count) {
        return root.language === "ru" ? "▾ ещё " + count : "▾ " + count + " more";
    }

    function noMatches(query) {
        return root.language === "ru"
            ? "Нет совпадений: «" + query + "»"
            : "No matches for “" + query + "”";
    }

    function filterLabel(key) {
        if (key === "apps")
            return root.t("Apps");
        if (key === "files")
            return root.t("Files");
        return root.t("All");
    }

    /// Whether ↑/↓ should walk the history instead of the selection: Up on an
    /// empty field opens it only from the first row (otherwise it moves the
    /// selection), Down only once inside, and Ctrl+P/N stay on the list.
    function wantsHistory(delta) {
        if (root.historyIndex >= 0)
            return true;
        return delta < 0 && field.text.length === 0 && root.currentIndex === 0
            && (feagle.history || []).length > 0;
    }

    /// Walk the history: -1 is the empty input, 0 the newest query.
    function historyStep(delta) {
        const entries = feagle.history || [];
        if (entries.length === 0)
            return;
        const next = delta < 0
            ? Math.min(entries.length - 1, root.historyIndex + 1)
            : Math.max(-1, root.historyIndex - 1);
        root.historyIndex = next;
        field.text = next < 0 ? "" : entries[next];
        field.cursorPosition = field.text.length;
    }

    /// Activate a result and remember the query that found it.
    function run(item) {
        if (!item)
            return;
        const text = String(feagle.query || "").trim();
        if (text.length > 0)
            feagle.addHistory(text);
        feagle.activateItem(item);
    }

    /// Whether a path is pinned (Ctrl+D toggles it).
    function isFavorite(path) {
        const list = feagle.guiConfig.favorites;
        return !!list && list.indexOf(path) >= 0;
    }

    function toggleFavorite() {
        const item = feagle.visibleResults[root.currentIndex];
        if (!item || item.kind !== "file") {
            root.showFlash(root.t("Only files can be pinned"));
            return;
        }
        const list = (feagle.guiConfig.favorites || []).slice();
        const at = list.indexOf(item.path);
        const pinning = at < 0;
        if (pinning)
            list.push(item.path);
        else
            list.splice(at, 1);
        feagle.setGui("favorites", list);
        root.showFlash(root.t(pinning ? "Pinned" : "Unpinned"));
    }

    /// A setting's current value: gui.toml if it answered, else the schema default.
    function settingValue(key) {
        const value = feagle.guiConfig[key];
        if (value !== undefined && value !== null)
            return value;
        const meta = feagle.guiSchema[key];
        return meta ? meta.default : undefined;
    }

    /// `settingValue` with a local fallback, for bindings that must render before
    /// the daemon has answered.
    function setting(key, fallback) {
        const value = root.settingValue(key);
        return value !== undefined && value !== null ? value : fallback;
    }

    /// Persist one setting; the live knobs are applied at once. The rest are
    /// bindings on `feagle.guiConfig`, so the reply re-renders them by itself.
    function changeSetting(key, value) {
        feagle.setGui(key, value);
        if (key === "default_filter")
            feagle.setFilter(value);
        else if (key === "apps_limit")
            feagle.appsLimit = value;
        else if (key === "debounce_ms")
            feagle.debounceMs = value;
    }

    /// Move an integer setting one step, clamped to the schema's range.
    function stepSetting(key, direction) {
        const meta = feagle.guiSchema[key] || ({});
        const min = meta.min !== undefined ? meta.min : 0;
        const max = meta.max !== undefined ? meta.max : 1000;
        const step = (key === "preview_width" || key === "debounce_ms") ? 20 : 1;
        const next = Number(root.settingValue(key)) + direction * step;
        root.changeSetting(key, Math.max(min, Math.min(max, next)));
    }

    function fileIcon(path) {
        const name = String(path).split("/").pop();
        const dot = name.lastIndexOf(".");
        const ext = dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
        const icons = {
            pdf: "application-pdf",
            doc: "x-office-document", docx: "x-office-document", odt: "x-office-document",
            xls: "x-office-spreadsheet", xlsx: "x-office-spreadsheet", ods: "x-office-spreadsheet",
            ppt: "x-office-presentation", pptx: "x-office-presentation", odp: "x-office-presentation",
            png: "image-x-generic", jpg: "image-x-generic", jpeg: "image-x-generic",
            webp: "image-x-generic", gif: "image-x-generic", tiff: "image-x-generic",
            bmp: "image-x-generic", svg: "image-x-generic",
            mp4: "video-x-generic", mkv: "video-x-generic", webm: "video-x-generic",
            mov: "video-x-generic", avi: "video-x-generic",
            mp3: "audio-x-generic", flac: "audio-x-generic", ogg: "audio-x-generic",
            wav: "audio-x-generic", m4a: "audio-x-generic",
            zip: "package-x-generic", tar: "package-x-generic", gz: "package-x-generic",
            xz: "package-x-generic", "7z": "package-x-generic", rar: "package-x-generic",
            md: "text-markdown", rst: "text-x-generic", txt: "text-x-generic",
            py: "text-x-python", js: "text-x-javascript", ts: "text-x-javascript",
            json: "application-json", html: "text-html", css: "text-css",
            sh: "text-x-script", c: "text-x-csrc", h: "text-x-chdr", cpp: "text-x-c++src",
            rs: "text-x-rust", go: "text-x-go", java: "text-x-java"
        };
        return icons[ext] || "text-x-generic";
    }

    /// `$HOME` is shown as `~` (display only; opening still uses the real path).
    /// Ctrl+T switches to full paths.
    function shorten(value) {
        const text = String(value || "");
        if (root.fullPaths || root.home.length === 0 || !text.startsWith(root.home))
            return text;
        return "~" + text.slice(root.home.length);
    }

    function primaryLabel(item) {
        return item.kind === "app" ? item.name : root.shorten(item.path);
    }

    // The themed icon path, or "" when the theme has no such icon (no warnings).
    function iconSource(item) {
        if (item.kind === "app")
            return Quickshell.iconPath(item.icon || "", true);
        return Quickshell.iconPath(fileIcon(item.path), true);
    }

    // Shown in the drawn fallback badge: the app initial, or the file extension.
    function fallbackLabel(item) {
        if (item.kind === "app") {
            const name = String(item.name || "").trim();
            return name.length > 0 ? name.charAt(0).toUpperCase() : "?";
        }
        const base = String(item.path).split("/").pop();
        const dot = base.lastIndexOf(".");
        const ext = dot > 0 ? base.slice(dot + 1).toUpperCase() : "";
        return ext.length > 0 ? ext.slice(0, 4) : "•";
    }

    function secondaryLabel(item) {
        return item.kind === "app" ? "" : (item.snippet || "");
    }

    function fmtTime(seconds) {
        const total = Math.round(seconds);
        const minutes = Math.floor(total / 60);
        const rest = total % 60;
        return minutes + ":" + (rest < 10 ? "0" : "") + rest;
    }

    function positionLabel(item) {
        if (item.kind === "app")
            return "";
        if (item.time !== null && item.time !== undefined)
            return fmtTime(item.time);
        if (item.page !== null && item.page !== undefined)
            return (root.language === "ru" ? "стр. " : "p. ") + item.page;
        if (item.line !== null && item.line !== undefined)
            return ":" + item.line;
        return "";
    }

    function move(delta) {
        if (feagle.visibleResults.length === 0)
            return;
        root.currentIndex = Math.max(
            0, Math.min(root.currentIndex + delta, feagle.visibleResults.length - 1));
    }

    function fmtSize(bytes) {
        if (bytes === undefined || bytes === null)
            return "";
        const units = ["B", "KB", "MB", "GB"];
        var value = bytes;
        var unit = 0;
        while (value >= 1024 && unit < units.length - 1) {
            value /= 1024;
            unit += 1;
        }
        return (unit === 0 ? value : value.toFixed(1)) + " " + units[unit];
    }

    function fmtDate(seconds) {
        if (!seconds)
            return "";
        return new Date(seconds * 1000).toLocaleString(Qt.locale(), Locale.ShortFormat);
    }

    // ---- keyboard helpers --------------------------------------------------
    function deleteWord() {
        const cursor = field.cursorPosition;
        if (cursor === 0)
            return;
        const before = field.text.slice(0, cursor).replace(/\s*\S+\s*$/, "");
        field.text = before + field.text.slice(cursor);
        field.cursorPosition = before.length;
    }

    function copyTarget() {
        const item = feagle.visibleResults[root.currentIndex];
        if (!item)
            return;
        const value = item.kind === "app" ? (item.exec || []).join(" ") : item.path;
        if (!value)
            return;
        Quickshell.clipboardText = value;
        root.showFlash(root.t("Copied ") + root.shorten(value));
    }

    /// Open the folder that contains the selection (files only).
    function openFolder() {
        const item = feagle.visibleResults[root.currentIndex];
        if (!item)
            return;
        if (item.kind !== "file") {
            root.run(item);
            root.closeLauncher();
            return;
        }
        const parts = String(item.path).split("/");
        parts.pop();
        Quickshell.execDetached(["xdg-open", parts.join("/") || "/"]);
        root.closeLauncher();
    }

    function showFlash(message) {
        root.flash = message;
        flashTimer.restart();
    }

    // ---- preview helpers ---------------------------------------------------
    /// Query words worth highlighting (letters/digits only, so no escaping traps).
    function queryTerms() {
        return String(feagle.query || "")
            .split(/\s+/)
            .filter((word) => /^[\w\u0400-\u04ff]{2,}$/u.test(word));
    }

    function escapeHtml(value) {
        return String(value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
    }

    /// One preview line as markup, with every query word emphasised.
    function markLine(raw) {
        var out = escapeHtml(String(raw).replace(/[\u0001\u0002]/g, ""));
        const terms = root.queryTerms();
        for (var i = 0; i < terms.length; i += 1) {
            const pattern = new RegExp("(" + terms[i] + ")", "gi");
            out = out.replace(pattern, "\u0001$1\u0002");
        }
        return out.split("\u0001").join("<b><font color=\"" + root.accent + "\">")
            .split("\u0002").join("</font></b>");
    }

    // The preview text as one row per line, so matches can be highlighted.
    function previewLines() {
        const preview = feagle.preview;
        if (!preview || preview.kind !== "text" || !preview.text)
            return [];
        const start = preview.start_line || 1;
        const raw = String(preview.text).split("\n");
        const rows = [];
        for (var i = 0; i < raw.length; i += 1) {
            const number = start + i;
            rows.push({
                number: number,
                markup: root.markLine(raw[i]),
                current: preview.line === number
            });
        }
        return rows;
    }

    // Ask for a preview of whatever is currently selected.
    function syncPreview() {
        // A hidden pane must not cost a daemon round-trip (or a PDF render).
        const item = root.showPreview
            ? (feagle.visibleResults[root.currentIndex] || null) : null;
        feagle.requestPreview(item);
    }

    // The muted second line of the preview header.
    function previewMeta(item, info) {
        if (!item)
            return "";
        if (item.kind === "app")
            return root.t("Application");
        var parts = [];
        if (info && info.size !== undefined)
            parts.push(fmtSize(info.size));
        if (info && info.mtime)
            parts.push(fmtDate(info.mtime));
        if (info && info.kind === "text" && info.start_line) {
            const lines = String(info.text || "").split("\n").length;
            const where = info.page ? root.t("page") + " " + info.page + ", " : "";
            parts.push(where + root.t("lines") + " " + info.start_line + "–"
                + (info.start_line + lines - 1));
        }
        return parts.join(" · ");
    }

    // What the pane shows when there is no text or image preview.
    function previewDetail(item, info) {
        if (!item)
            return "";
        if (item.kind === "app")
            return (item.exec || []).join(" ");
        if (feagle.previewError)
            return root.t("No preview: ") + feagle.previewError;
        if (info && info.kind === "meta")
            return root.shorten(item.path) + (info.reason ? "\n\n" + info.reason : "");
        if (!info)
            return root.t("Loading preview…");
        return root.shorten(item.path);
    }

    function openLauncher() {
        root.open = true;
        root.currentIndex = 0;
        root.daemonSlow = false;
        root.flash = "";
        root.settingsOpen = false;
        root.historyIndex = -1;
        field.text = "";
        // Pick up gui.toml (and any hand edit) on every open.
        feagle.setFilter(root.setting("default_filter", "all"));
        feagle.appsLimit = root.setting("apps_limit", 8);
        feagle.debounceMs = root.setting("debounce_ms", 120);
        feagle.reset();
        root.syncPreview();
        field.forceActiveFocus();
    }

    function closeLauncher() {
        root.open = false;
    }

    IpcHandler {
        target: "feagle-launcher"
        function toggle(): void { root.open ? root.closeLauncher() : root.openLauncher(); }
        function show(): void { root.openLauncher(); }
        function hide(): void { root.closeLauncher(); }
        /// Open the launcher with a query already typed (handy for hotkeys).
        function search(text: string): void {
            root.openLauncher();
            field.text = text;
        }
    }

    // ---- backdrop ----------------------------------------------------------
    Rectangle {
        anchors.fill: parent
        color: "#000000"
        opacity: root.open ? 0.5 : 0
        Behavior on opacity { NumberAnimation { duration: 120 } }
        MouseArea { anchors.fill: parent; onClicked: root.closeLauncher() }
    }

    // ---- card --------------------------------------------------------------
    Rectangle {
        id: card
        readonly property bool previewPane: root.open && root.showPreview
            && feagle.visibleResults.length > 0
        width: Math.min(card.previewPane ? root.cardPreviewWidth : root.cardWidth,
                        parent.width - 48)
        height: Math.min(card.previewPane ? root.cardPreviewHeight : root.cardHeight,
                         parent.height - 96)
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: Math.max(40, parent.height * root.topMarginRatio)
        radius: 14
        color: root.base
        border.width: 1
        border.color: root.border
        opacity: root.open ? 1 : 0
        scale: root.open ? 1 : 0.98
        Behavior on opacity { NumberAnimation { duration: 120 } }
        Behavior on scale { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }
        Behavior on width { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
        Behavior on height { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }

        // Swallow clicks so they never reach the backdrop.
        MouseArea { anchors.fill: parent }

        // ---- search row ----------------------------------------------------
        Item {
            id: searchRow
            anchors { top: parent.top; left: parent.left; right: parent.right }
            height: 56

            // The feagle mark (quickshell/feagle.svg), loaded next to the repo.
            Item {
                id: searchGlyph
                anchors.verticalCenter: parent.verticalCenter
                anchors.left: parent.left
                anchors.leftMargin: 15
                width: 28
                height: 28

                Image {
                    anchors.centerIn: parent
                    width: 28
                    height: 28
                    source: Qt.resolvedUrl("feagle.svg")
                    fillMode: Image.PreserveAspectFit
                    smooth: true
                    asynchronous: true
                }
            }

            Text {
                anchors.verticalCenter: parent.verticalCenter
                anchors.left: searchGlyph.right
                anchors.leftMargin: 12
                color: root.muted
                font.pixelSize: 17
                text: root.t("Search files and applications…")
                visible: field.text.length === 0
            }

            TextInput {
                id: field
                anchors.verticalCenter: parent.verticalCenter
                anchors.left: searchGlyph.right
                anchors.right: chips.left
                anchors.leftMargin: 12
                anchors.rightMargin: 14
                color: root.text
                font.pixelSize: 17
                clip: true
                selectByMouse: true
                onTextChanged: {
                    root.currentIndex = 0;
                    // Typing leaves the history walk (an entry set by it matches).
                    if (root.historyIndex >= 0
                            && text !== (feagle.history[root.historyIndex] || ""))
                        root.historyIndex = -1;
                    feagle.setQuery(text);
                }
                Keys.onPressed: (event) => {
                    const ctrl = (event.modifiers & Qt.ControlModifier) !== 0;
                    if (event.key === Qt.Key_Down) {
                        if (!ctrl && root.wantsHistory(1))
                            root.historyStep(1);
                        else
                            root.move(1);
                        event.accepted = true;
                    } else if (ctrl && event.key === Qt.Key_N) {
                        root.move(1); event.accepted = true;
                    } else if (event.key === Qt.Key_Up) {
                        if (!ctrl && root.wantsHistory(-1))
                            root.historyStep(-1);
                        else
                            root.move(-1);
                        event.accepted = true;
                    } else if (ctrl && event.key === Qt.Key_P) {
                        root.move(-1); event.accepted = true;
                    } else if (event.key === Qt.Key_PageDown) {
                        root.move(6); event.accepted = true;
                    } else if (event.key === Qt.Key_PageUp) {
                        root.move(-6); event.accepted = true;
                    } else if (event.key === Qt.Key_Home) {
                        root.currentIndex = 0; event.accepted = true;
                    } else if (event.key === Qt.Key_End) {
                        root.currentIndex = Math.max(0, feagle.visibleResults.length - 1);
                        event.accepted = true;
                    } else if ((event.modifiers & Qt.AltModifier) !== 0
                               && event.key >= Qt.Key_1 && event.key <= Qt.Key_9) {
                        const at = event.key - Qt.Key_1;
                        if (at < feagle.visibleResults.length) {
                            root.currentIndex = at;
                            list.positionViewAtIndex(at, ListView.Contain);
                        }
                        event.accepted = true;
                    } else if (event.key === Qt.Key_Tab) {
                        feagle.cycleFilter((event.modifiers & Qt.ShiftModifier) !== 0);
                        root.currentIndex = 0;
                        event.accepted = true;
                    } else if (event.key === Qt.Key_Backtab) {
                        feagle.cycleFilter(true);
                        root.currentIndex = 0;
                        event.accepted = true;
                    } else if (ctrl && event.key === Qt.Key_U) {
                        field.text = ""; event.accepted = true;
                    } else if (ctrl && event.key === Qt.Key_W) {
                        root.deleteWord(); event.accepted = true;
                    } else if (ctrl && event.key === Qt.Key_A) {
                        field.selectAll(); event.accepted = true;
                    } else if (ctrl && event.key === Qt.Key_C) {
                        root.copyTarget(); event.accepted = true;
                    } else if (ctrl && event.key === Qt.Key_T) {
                        root.fullPaths = !root.fullPaths;
                        root.showFlash(root.t(root.fullPaths
                            ? "Full paths" : "Short paths (~)"));
                        event.accepted = true;
                    } else if (ctrl && event.key === Qt.Key_D) {
                        root.toggleFavorite(); event.accepted = true;
                    } else if (ctrl && (event.key === Qt.Key_Return || event.key === Qt.Key_Enter)) {
                        root.openFolder(); event.accepted = true;
                    } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                        if (!root.settingsOpen) {
                            root.run(feagle.visibleResults[root.currentIndex]);
                            root.closeLauncher();
                        }
                        event.accepted = true;
                    } else if (event.key === Qt.Key_Escape) {
                        if (root.settingsOpen)
                            root.settingsOpen = false;
                        else
                            root.closeLauncher();
                        event.accepted = true;
                    }
                }
            }

            // Filter chips: click or Tab. The active one is highlighted.
            Row {
                id: chips
                anchors.verticalCenter: parent.verticalCenter
                anchors.right: parent.right
                anchors.rightMargin: 18
                spacing: 4

                Repeater {
                    model: [
                        { key: "all" },
                        { key: "apps" },
                        { key: "files" }
                    ]

                    delegate: Rectangle {
                        id: chip
                        required property var modelData
                        readonly property bool active: feagle.filter === chip.modelData.key
                        width: chipText.implicitWidth + 18
                        height: 24
                        radius: 12
                        color: chip.active ? root.selected
                                           : (chipHover.hovered ? root.hovered : "transparent")
                        border.width: chip.active ? 0 : 1
                        border.color: root.border

                        Text {
                            id: chipText
                            anchors.centerIn: parent
                            color: chip.active ? root.text : root.muted
                            font.pixelSize: 11
                            font.bold: chip.active
                            text: root.filterLabel(chip.modelData.key)
                        }

                        HoverHandler { id: chipHover }
                        MouseArea {
                            anchors.fill: parent
                            onClicked: {
                                feagle.setFilter(chip.modelData.key);
                                root.currentIndex = 0;
                                field.forceActiveFocus();
                            }
                        }
                    }
                }
            }
        }

        Rectangle {
            id: dividerTop
            anchors { top: searchRow.bottom; left: parent.left; right: parent.right }
            height: 1
            color: root.border
            visible: feagle.visibleResults.length > 0
        }

        // ---- footer --------------------------------------------------------
        Item {
            id: footer
            anchors { bottom: parent.bottom; left: parent.left; right: parent.right }
            height: 34

            Text {
                anchors.verticalCenter: parent.verticalCenter
                anchors.left: parent.left
                anchors.leftMargin: 18
                anchors.right: hints.left
                anchors.rightMargin: 12
                elide: Text.ElideRight
                color: root.flash.length > 0 ? root.accent : root.muted
                font.pixelSize: 11
                text: {
                    if (root.flash.length > 0)
                        return root.flash;
                    if (root.daemonSlow && !feagle.connected)
                        return root.t("queryd is not reachable — start feagle-queryd");
                    if (feagle.error)
                        return "queryd: " + feagle.error;
                    const count = feagle.visibleResults.length;
                    const kind = feagle.mode === "hybrid"
                        ? root.t("semantic") : root.t("keyword");
                    const filter = feagle.filter === "all"
                        ? "" : root.filterLabel(feagle.filter) + " · ";
                    const took = Math.round(feagle.tookMs)
                        + (root.language === "ru" ? " мс" : " ms");
                    return filter + root.resultsText(count) + " · " + kind + " · " + took;
                }
            }

            Text {
                id: hints
                anchors.verticalCenter: parent.verticalCenter
                anchors.right: gear.left
                anchors.rightMargin: 8
                width: Math.min(implicitWidth, parent.width - 240)
                horizontalAlignment: Text.AlignRight
                elide: Text.ElideLeft
                color: root.muted
                font.pixelSize: 11
                text: root.t("alt+1…9  ↑↓ history  tab filter  ↵ open  ^c copy  ^d pin  ^t paths  esc close")
            }

            // The settings panel (gui.toml through the daemon).
            Rectangle {
                id: gear
                anchors.verticalCenter: parent.verticalCenter
                anchors.right: parent.right
                anchors.rightMargin: 12
                width: 26
                height: 26
                radius: 7
                color: gearHover.hovered || root.settingsOpen ? root.hovered : "transparent"

                Text {
                    anchors.centerIn: parent
                    text: "⚙"
                    color: root.muted
                    font.pixelSize: 14
                }
                HoverHandler { id: gearHover }
                MouseArea {
                    anchors.fill: parent
                    onClicked: root.settingsOpen = !root.settingsOpen
                }
            }
        }

        Rectangle {
            id: dividerBottom
            anchors { bottom: footer.top; left: parent.left; right: parent.right }
            height: 1
            color: root.border
            visible: feagle.visibleResults.length > 0
        }

        // ---- body: result list + preview pane ------------------------------
        Item {
            id: body
            anchors.top: dividerTop.bottom
            anchors.bottom: dividerBottom.top
            anchors.left: parent.left
            anchors.right: parent.right

            ListView {
                id: list
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                anchors.left: parent.left
                width: card.previewPane ? previewPane.x - 1 : parent.width
                clip: true
                model: feagle.visibleResults
                currentIndex: root.currentIndex
                onCurrentIndexChanged: {
                    if (currentIndex >= 0)
                        positionViewAtIndex(currentIndex, ListView.Contain);
                    root.syncPreview();
                }
                delegate: Item {
                    id: rowItem
                    required property var modelData
                    required property int index
                    /// First row of its kind: the group label is drawn above it.
                    readonly property bool groupStart: rowItem.index === 0
                        || list.model[rowItem.index - 1].kind !== rowItem.modelData.kind
                    width: list.width
                    height: (rowItem.groupStart ? 26 : 0) + (root.compactRows ? 34 : 50)

                    Text {
                        anchors.left: parent.left
                        anchors.leftMargin: 18
                        anchors.top: parent.top
                        anchors.topMargin: 10
                        visible: rowItem.groupStart
                        color: root.muted
                        font.pixelSize: 10
                        font.capitalization: Font.AllUppercase
                        font.letterSpacing: 0.7
                        text: rowItem.modelData.kind === "app"
                            ? root.t("Applications") : root.t("Files")
                    }

                    Rectangle {
                        id: row
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        height: root.compactRows ? 34 : 50
                        color: rowItem.index === root.currentIndex
                            ? root.selected
                            : (hover.hovered ? root.hovered : "transparent")

                        // Real themed icon when the theme has one, else a drawn badge.
                        IconImage {
                            id: rowIcon
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.left: parent.left
                            anchors.leftMargin: 18
                            implicitSize: 22
                            visible: source !== ""
                            source: root.iconSource(rowItem.modelData)
                        }

                        Rectangle {
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.left: parent.left
                            anchors.leftMargin: 18
                            width: 22
                            height: 22
                            radius: 6
                            color: root.hovered
                            visible: rowIcon.source === ""
                            Text {
                                anchors.centerIn: parent
                                color: root.muted
                                font.pixelSize: 9
                                font.bold: true
                                text: root.fallbackLabel(rowItem.modelData)
                            }
                        }

                        Column {
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.left: rowIcon.right
                            anchors.leftMargin: 12
                            anchors.right: rowPos.left
                            anchors.rightMargin: 12
                            spacing: 1

                            Text {
                                width: parent.width
                                color: root.text
                                font.pixelSize: 14
                                textFormat: Text.StyledText
                                elide: Text.ElideMiddle
                                text: root.markLine(root.primaryLabel(rowItem.modelData))
                            }
                            Text {
                                width: parent.width
                                color: root.muted
                                font.pixelSize: 11
                                textFormat: Text.StyledText
                                elide: Text.ElideRight
                                visible: text.length > 0 && root.rowColumns >= 2
                                    && !root.compactRows
                                text: root.markLine(root.secondaryLabel(rowItem.modelData))
                            }
                        }

                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.right: rowPos.left
                            anchors.rightMargin: 8
                            visible: rowItem.modelData.kind === "file"
                                && root.isFavorite(rowItem.modelData.path)
                            text: "★"
                            color: root.accent
                            font.pixelSize: 11
                        }

                        Text {
                            id: rowPos
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.right: parent.right
                            anchors.rightMargin: 18
                            color: root.muted
                            font.pixelSize: 11
                            visible: root.rowColumns >= 3
                            text: root.positionLabel(rowItem.modelData)
                        }

                        HoverHandler {
                            id: hover
                            onHoveredChanged: {
                                if (hovered)
                                    root.currentIndex = rowItem.index;
                            }
                        }

                        MouseArea {
                            anchors.fill: parent
                            onClicked: {
                                root.run(rowItem.modelData);
                                root.closeLauncher();
                            }
                        }
                    }
                }
            }

            // Thin scroll indicator for the list.
            Rectangle {
                id: scrollbar
                width: 3
                radius: 1.5
                color: root.muted
                opacity: 0.35
                x: list.width - 7
                visible: list.contentHeight > list.height + 2
                height: Math.max(28, body.height * body.height / list.contentHeight)
                y: list.contentY / Math.max(1, list.contentHeight - list.height)
                    * Math.max(0, body.height - scrollbar.height)
            }

            // "N more below" pill, so a clipped list never looks finished.
            Rectangle {
                id: moreBelow
                readonly property int hiddenBelow: list.contentHeight > list.contentY + list.height + 2
                    ? Math.max(1, list.count - 1 - list.indexAt(list.width / 2, list.height - 2))
                    : 0
                visible: hiddenBelow > 0
                anchors.horizontalCenter: list.horizontalCenter
                anchors.bottom: list.bottom
                anchors.bottomMargin: 8
                width: moreText.implicitWidth + 18
                height: 20
                radius: 10
                color: root.base
                border.width: 1
                border.color: root.border

                Text {
                    id: moreText
                    anchors.centerIn: parent
                    color: root.muted
                    font.pixelSize: 10
                    text: root.moreText(moreBelow.hiddenBelow)
                }
            }

            Rectangle {
                id: dividerRight
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                x: previewPane.x - 1
                width: 1
                color: root.border
                visible: card.previewPane
            }

            // ---- preview pane ----------------------------------------------
            Item {
                id: previewPane
                width: root.previewWidth
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                anchors.right: parent.right
                visible: card.previewPane
                clip: true

                readonly property var item: feagle.visibleResults[root.currentIndex] || null
                readonly property var info: feagle.preview
                readonly property bool isText: info !== null && info.kind === "text"
                readonly property bool isImage: info !== null && info.kind === "image"

                IconImage {
                    id: previewIcon
                    anchors.top: parent.top
                    anchors.left: parent.left
                    anchors.topMargin: 16
                    anchors.leftMargin: 18
                    implicitSize: 40
                    visible: source !== ""
                    source: previewPane.item ? root.iconSource(previewPane.item) : ""
                }

                Rectangle {
                    anchors.top: parent.top
                    anchors.left: parent.left
                    anchors.topMargin: 16
                    anchors.leftMargin: 18
                    width: 40
                    height: 40
                    radius: 10
                    color: root.hovered
                    visible: previewIcon.source === ""
                    Text {
                        anchors.centerIn: parent
                        color: root.muted
                        font.pixelSize: 14
                        font.bold: true
                        text: previewPane.item ? root.fallbackLabel(previewPane.item) : ""
                    }
                }

                Text {
                    id: previewTitle
                    anchors.top: parent.top
                    anchors.left: previewIcon.right
                    anchors.right: parent.right
                    anchors.topMargin: 19
                    anchors.leftMargin: 14
                    anchors.rightMargin: 18
                    color: root.text
                    font.pixelSize: 14
                    font.bold: true
                    elide: Text.ElideMiddle
                    text: previewPane.item ? root.primaryLabel(previewPane.item) : ""
                }

                Text {
                    id: previewMeta
                    anchors.top: previewTitle.bottom
                    anchors.left: previewTitle.left
                    anchors.right: previewTitle.right
                    anchors.topMargin: 3
                    color: root.muted
                    font.pixelSize: 11
                    elide: Text.ElideMiddle
                    text: root.previewMeta(previewPane.item, previewPane.info)
                }

                // Text preview: numbered lines, query words highlighted, wheel scroll.
                Flickable {
                    id: previewScroll
                    anchors.top: previewMeta.bottom
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    anchors.topMargin: 12
                    anchors.leftMargin: 18
                    anchors.rightMargin: 18
                    anchors.bottomMargin: 12
                    visible: previewPane.isText
                    clip: true
                    contentWidth: width
                    contentHeight: previewText.height
                    boundsBehavior: Flickable.StopAtBounds
                    opacity: visible ? 1 : 0
                    Behavior on opacity { NumberAnimation { duration: 140 } }

                    Column {
                        id: previewText
                        width: previewScroll.width
                        spacing: 0

                        Repeater {
                            model: root.previewLines()
                            delegate: Item {
                                id: lineRow
                                required property var modelData
                                width: previewText.width
                                height: Math.max(17, lineText.implicitHeight + 2)

                                Rectangle {
                                    anchors.fill: parent
                                    color: lineRow.modelData.current ? root.selected : "transparent"
                                }
                                Text {
                                    anchors.left: parent.left
                                    anchors.top: parent.top
                                    anchors.topMargin: 1
                                    width: 32
                                    horizontalAlignment: Text.AlignRight
                                    color: root.muted
                                    font.pixelSize: 10
                                    font.family: "monospace"
                                    text: lineRow.modelData.number
                                }
                                Text {
                                    id: lineText
                                    anchors.left: parent.left
                                    anchors.leftMargin: 40
                                    anchors.right: parent.right
                                    anchors.top: parent.top
                                    color: lineRow.modelData.current ? root.text : root.muted
                                    font.pixelSize: 11
                                    font.family: "monospace"
                                    textFormat: Text.StyledText
                                    wrapMode: Text.Wrap
                                    text: lineRow.modelData.markup.length > 0
                                        ? lineRow.modelData.markup : " "
                                }
                            }
                        }
                    }
                }

                // Thin scroll indicator for the preview.
                Rectangle {
                    width: 3
                    radius: 1.5
                    color: root.muted
                    opacity: 0.3
                    x: parent.width - 7
                    visible: previewScroll.visible
                        && previewScroll.contentHeight > previewScroll.height + 2
                    height: Math.max(24, previewScroll.height * previewScroll.height
                        / previewScroll.contentHeight)
                    y: previewScroll.y + previewScroll.contentY
                        / Math.max(1, previewScroll.contentHeight - previewScroll.height)
                        * Math.max(0, previewScroll.height - height)
                }

                Image {
                    id: previewImage
                    anchors.top: previewMeta.bottom
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: previewSize.bottom
                    anchors.margins: 18
                    visible: previewPane.isImage
                    source: previewPane.isImage ? "file://" + previewPane.info.path : ""
                    fillMode: Image.PreserveAspectFit
                    asynchronous: true
                    sourceSize.width: 640
                    sourceSize.height: 480
                }

                Text {
                    id: previewSize
                    anchors.bottom: parent.bottom
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottomMargin: 12
                    anchors.leftMargin: 18
                    anchors.rightMargin: 18
                    horizontalAlignment: Text.AlignHCenter
                    color: root.muted
                    font.pixelSize: 10
                    visible: previewPane.isImage
                    text: previewPane.info ? root.fmtSize(previewPane.info.size) : ""
                }

                // Apps and files without a text/image preview.
                Text {
                    anchors.top: previewMeta.bottom
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.topMargin: 16
                    anchors.leftMargin: 18
                    anchors.rightMargin: 18
                    visible: !previewPane.isText && !previewPane.isImage
                    color: root.muted
                    font.pixelSize: 11
                    font.family: previewPane.item && previewPane.item.kind === "app"
                        ? "monospace" : ""
                    wrapMode: Text.WrapAnywhere
                    text: root.previewDetail(previewPane.item, previewPane.info)
                }
            }

            Column {
                anchors.centerIn: parent
                width: parent.width - 48
                spacing: 18
                visible: feagle.visibleResults.length === 0

                Image {
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: 96
                    height: 96
                    source: Qt.resolvedUrl("feagle.svg")
                    fillMode: Image.PreserveAspectFit
                    smooth: true
                    asynchronous: true
                }

                Text {
                    width: parent.width
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                    color: root.muted
                    font.pixelSize: 13
                    text: {
                        if (feagle.query.length > 0)
                            return root.noMatches(feagle.query);
                        if (feagle.filter === "files")
                            return root.t("Type to search files");
                        return root.t("Type to search your files and applications");
                    }
                }
            }
        }

        // ---- settings (gui.toml; saved through the daemon on every change) ---
        Rectangle {
            id: settingsPanel
            anchors.fill: parent
            radius: 12
            color: root.base
            visible: root.settingsOpen

            Text {
                id: settingsTitle
                anchors { left: parent.left; leftMargin: 18; top: parent.top; topMargin: 16 }
                text: root.t("Settings")
                color: root.text
                font.pixelSize: 12
                font.bold: true
                font.capitalization: Font.AllUppercase
                font.letterSpacing: 0.7
            }

            Text {
                anchors { left: settingsTitle.right; leftMargin: 10; baseline: settingsTitle.baseline }
                text: root.t("saved on change · gui.toml")
                color: root.muted
                font.pixelSize: 11
            }

            Rectangle {
                anchors { right: parent.right; rightMargin: 14; top: parent.top; topMargin: 10 }
                width: 28
                height: 28
                radius: 8
                color: settingsCloseHover.hovered ? root.hovered : "transparent"

                Text {
                    anchors.centerIn: parent
                    text: "×"
                    color: root.muted
                    font.pixelSize: 16
                }
                HoverHandler { id: settingsCloseHover }
                MouseArea {
                    anchors.fill: parent
                    onClicked: root.settingsOpen = false
                }
            }

            Text {
                anchors.centerIn: parent
                width: parent.width - 48
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
                color: root.muted
                font.pixelSize: 12
                visible: Object.keys(feagle.guiSchema).length === 0
                text: root.t("Settings need the query daemon: ")
                    + (feagle.error || root.t("queryd is not reachable — start feagle-queryd"))
            }

            // One row per setting; the control follows the schema's `kind`, so the
            // ranges and choices stay in gui.py and are not repeated here.
            ListView {
                id: settingsList
                anchors {
                    top: settingsTitle.bottom; topMargin: 14
                    left: parent.left; right: parent.right; bottom: parent.bottom
                }
                clip: true
                model: root.settingsOrder
                spacing: 2

                delegate: Item {
                    id: settingRow
                    required property string modelData
                    readonly property var meta: feagle.guiSchema[settingRow.modelData] || ({})
                    readonly property var current: root.settingValue(settingRow.modelData)
                    width: settingsList.width
                    height: 46

                    Text {
                        anchors {
                            left: parent.left; leftMargin: 18
                            verticalCenter: parent.verticalCenter
                        }
                        width: parent.width - 200
                        text: settingRow.meta.hint
                            ? (settingRow.meta.hint[root.language] || settingRow.meta.hint.en)
                            : settingRow.modelData
                        color: root.text
                        font.pixelSize: 12
                        elide: Text.ElideRight
                    }

                    // bool
                    Rectangle {
                        visible: settingRow.meta.kind === "bool"
                        anchors {
                            right: parent.right; rightMargin: 18
                            verticalCenter: parent.verticalCenter
                        }
                        width: 38
                        height: 20
                        radius: 10
                        color: settingRow.current ? root.accent : root.hovered
                        Behavior on color { ColorAnimation { duration: 120 } }

                        Rectangle {
                            width: 14
                            height: 14
                            radius: 7
                            anchors.verticalCenter: parent.verticalCenter
                            x: settingRow.current ? parent.width - width - 3 : 3
                            color: root.base
                            Behavior on x { NumberAnimation { duration: 120 } }
                        }
                        MouseArea {
                            anchors.fill: parent
                            onClicked: root.changeSetting(
                                settingRow.modelData, !settingRow.current)
                        }
                    }

                    // int: - value +
                    Row {
                        visible: settingRow.meta.kind === "int"
                        anchors {
                            right: parent.right; rightMargin: 18
                            verticalCenter: parent.verticalCenter
                        }
                        spacing: 6

                        Rectangle {
                            width: 22
                            height: 22
                            radius: 6
                            border.width: 1
                            border.color: root.border
                            color: stepDownHover.hovered ? root.hovered : "transparent"
                            Text {
                                anchors.centerIn: parent
                                text: "−"
                                color: root.muted
                                font.pixelSize: 13
                            }
                            HoverHandler { id: stepDownHover }
                            MouseArea {
                                anchors.fill: parent
                                onClicked: root.stepSetting(settingRow.modelData, -1)
                            }
                        }
                        Text {
                            width: 46
                            horizontalAlignment: Text.AlignHCenter
                            color: root.text
                            font.pixelSize: 12
                            text: settingRow.current === undefined
                                ? "—" : String(settingRow.current)
                        }
                        Rectangle {
                            width: 22
                            height: 22
                            radius: 6
                            border.width: 1
                            border.color: root.border
                            color: stepUpHover.hovered ? root.hovered : "transparent"
                            Text {
                                anchors.centerIn: parent
                                text: "+"
                                color: root.muted
                                font.pixelSize: 13
                            }
                            HoverHandler { id: stepUpHover }
                            MouseArea {
                                anchors.fill: parent
                                onClicked: root.stepSetting(settingRow.modelData, 1)
                            }
                        }
                    }

                    // str: one chip per choice
                    Row {
                        visible: settingRow.meta.kind === "str"
                        anchors {
                            right: parent.right; rightMargin: 18
                            verticalCenter: parent.verticalCenter
                        }
                        spacing: 4

                        Repeater {
                            model: settingRow.meta.choices || []
                            delegate: Rectangle {
                                id: choice
                                required property string modelData
                                readonly property bool active:
                                    settingRow.current === choice.modelData
                                width: choiceText.implicitWidth + 16
                                height: 22
                                radius: 11
                                border.width: choice.active ? 0 : 1
                                border.color: root.border
                                color: choice.active
                                    ? root.selected
                                    : (choiceHover.hovered ? root.hovered : "transparent")

                                Text {
                                    id: choiceText
                                    anchors.centerIn: parent
                                    text: choice.modelData
                                    color: choice.active ? root.text : root.muted
                                    font.pixelSize: 11
                                }
                                HoverHandler { id: choiceHover }
                                MouseArea {
                                    anchors.fill: parent
                                    onClicked: root.changeSetting(
                                        settingRow.modelData, choice.modelData)
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
