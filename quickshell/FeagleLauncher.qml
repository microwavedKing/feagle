// feagle launcher — one window, everything: applications first, then file hits,
// ranked semantically as you type (instant keyword results, then a semantic
// refresh). No second window, no separate "ask for a query" step.
//
// The feagle protocol lives in FeagleSearch.qml; this file only draws and keys.
//
//   Run the demo:      quickshell -p /path/to/feagle/quickshell
//   Toggle from a key: qs ipc call feagle-launcher toggle
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

    // ---- theme (edit these to match your shell) ----------------------------
    readonly property color base: "#1e1e2e"
    readonly property color border: "#313244"
    readonly property color text: "#cdd6f4"
    readonly property color muted: "#a6adc8"
    readonly property color selected: "#313244"
    readonly property color hovered: "#28283a"

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

    // ---- helpers -----------------------------------------------------------
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

    function primaryLabel(item) {
        return item.kind === "app" ? item.name : item.path;
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
            return "p. " + item.page;
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

    function baseName(path) {
        return String(path || "").split("/").pop();
    }

    function fmtDate(seconds) {
        if (!seconds)
            return "";
        return new Date(seconds * 1000).toLocaleString(Qt.locale(), Locale.ShortFormat);
    }

    // The preview text as one row per line, so the match can be highlighted.
    function previewLines() {
        const preview = feagle.preview;
        if (!preview || preview.kind !== "text" || !preview.text)
            return [];
        const start = preview.start_line || 1;
        const raw = String(preview.text).split("\n");
        const rows = [];
        for (var i = 0; i < raw.length; i += 1) {
            const number = start + i;
            rows.push({ number: number, text: raw[i], current: preview.line === number });
        }
        return rows;
    }

    // Ask for a preview of whatever is currently selected.
    function syncPreview() {
        feagle.requestPreview(feagle.visibleResults[root.currentIndex] || null);
    }

    function filterLabel() {
        return feagle.filter === "all" ? "" : feagle.filter + " · ";
    }

    // The muted second line of the preview header.
    function previewMeta(item, info) {
        if (!item)
            return "";
        if (item.kind === "app")
            return "Application";
        var parts = [];
        if (info && info.size !== undefined)
            parts.push(fmtSize(info.size));
        if (info && info.mtime)
            parts.push(fmtDate(info.mtime));
        if (info && info.kind === "text" && info.start_line) {
            const lines = String(info.text || "").split("\n").length;
            parts.push("lines " + info.start_line + "–" + (info.start_line + lines - 1));
        }
        return parts.join(" · ");
    }

    // What the pane shows when there is no text or image preview.
    function previewDetail(item, info) {
        if (!item)
            return "";
        if (item.kind === "app")
            return item.exec || "";
        if (feagle.previewError)
            return "No preview: " + feagle.previewError;
        if (info && info.kind === "meta")
            return item.path + (info.reason ? "\n\n" + info.reason : "");
        if (!info)
            return "Loading preview…";
        return item.path;
    }

    function openLauncher() {
        root.open = true;
        root.currentIndex = 0;
        root.daemonSlow = false;
        field.text = "";
        feagle.setFilter("all");
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
        readonly property bool previewPane: root.open && feagle.visibleResults.length > 0
        width: Math.min(card.previewPane ? 940 : 620, parent.width - 48)
        height: Math.min(card.previewPane ? 480 : 440, parent.height - 96)
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: Math.max(40, parent.height * 0.15)
        radius: 14
        color: root.base
        border.width: 1
        border.color: root.border
        opacity: root.open ? 1 : 0
        scale: root.open ? 1 : 0.98
        Behavior on opacity { NumberAnimation { duration: 120 } }
        Behavior on scale { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }

        // Swallow clicks so they never reach the backdrop.
        MouseArea { anchors.fill: parent }

        // Search field.
        Item {
            id: searchRow
            anchors { top: parent.top; left: parent.left; right: parent.right }
            height: 56

            // Drawn magnifier — no icon-theme dependency, always renders.
            Item {
                id: searchGlyph
                anchors.verticalCenter: parent.verticalCenter
                anchors.left: parent.left
                anchors.leftMargin: 18
                width: 18
                height: 18

                Rectangle {
                    x: 1
                    y: 1
                    width: 11
                    height: 11
                    radius: 5.5
                    color: "transparent"
                    border.width: 1.6
                    border.color: root.muted
                }
                Rectangle {
                    x: 10
                    y: 10
                    width: 2
                    height: 6
                    radius: 1
                    color: root.muted
                    rotation: 45
                    transformOrigin: Item.TopLeft
                }
            }

            Text {
                anchors.verticalCenter: parent.verticalCenter
                anchors.left: searchGlyph.right
                anchors.leftMargin: 12
                color: root.muted
                font.pixelSize: 17
                text: "Search files and applications…"
                visible: field.text.length === 0
            }

            TextInput {
                id: field
                anchors.verticalCenter: parent.verticalCenter
                anchors.left: searchGlyph.right
                anchors.right: parent.right
                anchors.leftMargin: 12
                anchors.rightMargin: 18
                color: root.text
                font.pixelSize: 17
                clip: true
                selectByMouse: true
                onTextChanged: {
                    root.currentIndex = 0;
                    feagle.setQuery(text);
                }
                Keys.onPressed: (event) => {
                    if (event.key === Qt.Key_Down) {
                        root.move(1); event.accepted = true;
                    } else if (event.key === Qt.Key_Up) {
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
                    } else if (event.key === Qt.Key_Tab) {
                        feagle.cycleFilter((event.modifiers & Qt.ShiftModifier) !== 0);
                        root.currentIndex = 0;
                        event.accepted = true;
                    } else if (event.key === Qt.Key_Backtab) {
                        feagle.cycleFilter(true);
                        root.currentIndex = 0;
                        event.accepted = true;
                    } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                        feagle.activate(root.currentIndex); root.closeLauncher();
                        event.accepted = true;
                    } else if (event.key === Qt.Key_Escape) {
                        root.closeLauncher(); event.accepted = true;
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

        // Footer with the mode and key hints.
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
                color: root.muted
                font.pixelSize: 11
                text: {
                    if (root.daemonSlow && !feagle.connected)
                        return "queryd is not reachable — start feagle-queryd";
                    if (feagle.error)
                        return "queryd: " + feagle.error;
                    const count = feagle.visibleResults.length;
                    const kind = feagle.mode === "hybrid" ? "semantic" : "keyword";
                    return root.filterLabel() + count + (count === 1 ? " result" : " results")
                        + " · " + kind + " · " + Math.round(feagle.tookMs) + " ms";
                }
            }

            Text {
                id: hints
                anchors.verticalCenter: parent.verticalCenter
                anchors.right: parent.right
                anchors.rightMargin: 18
                color: root.muted
                font.pixelSize: 11
                text: "tab filter  ↑↓  ↵ open  esc close"
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
                delegate: Rectangle {
                    id: row
                    required property var modelData
                    required property int index
                    width: list.width
                    height: 50
                    color: index === root.currentIndex
                        ? root.selected
                        : (hover.hovered ? root.hovered : "transparent")

                    // Real themed icon when the theme has one, else a drawn fallback.
                    IconImage {
                        id: rowIcon
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: parent.left
                        anchors.leftMargin: 18
                        implicitSize: 22
                        visible: source !== ""
                        source: root.iconSource(row.modelData)
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
                            text: root.fallbackLabel(row.modelData)
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
                            elide: Text.ElideMiddle
                            text: root.primaryLabel(row.modelData)
                        }
                        Text {
                            width: parent.width
                            color: root.muted
                            font.pixelSize: 11
                            elide: Text.ElideRight
                            visible: text.length > 0
                            text: root.secondaryLabel(row.modelData)
                        }
                    }

                    Text {
                        id: rowPos
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.right: parent.right
                        anchors.rightMargin: 18
                        color: root.muted
                        font.pixelSize: 11
                        text: root.positionLabel(row.modelData)
                    }

                    HoverHandler {
                        id: hover
                        onHoveredChanged: {
                            if (hovered)
                                root.currentIndex = row.index;
                        }
                    }

                    MouseArea {
                        anchors.fill: parent
                        onClicked: {
                            feagle.activateItem(row.modelData);
                            root.closeLauncher();
                        }
                    }
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
                width: 360
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

                // Text preview: numbered context lines, the match highlighted.
                Column {
                    id: previewText
                    anchors.top: previewMeta.bottom
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.topMargin: 14
                    anchors.leftMargin: 18
                    anchors.rightMargin: 18
                    spacing: 0
                    visible: previewPane.isText

                    Repeater {
                        model: root.previewLines()
                        delegate: Rectangle {
                            id: lineRow
                            required property var modelData
                            width: previewText.width
                            height: 17
                            color: lineRow.modelData.current ? root.selected : "transparent"

                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                width: 32
                                horizontalAlignment: Text.AlignRight
                                color: root.muted
                                font.pixelSize: 10
                                font.family: "monospace"
                                text: lineRow.modelData.number
                            }
                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.left: parent.left
                                anchors.leftMargin: 40
                                anchors.right: parent.right
                                color: lineRow.modelData.current ? root.text : root.muted
                                font.pixelSize: 11
                                font.family: "monospace"
                                elide: Text.ElideRight
                                text: lineRow.modelData.text.length > 0 ? lineRow.modelData.text : " "
                            }
                        }
                    }
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
                    font.family: previewPane.item && previewPane.item.kind === "app" ? "monospace" : ""
                    wrapMode: Text.WrapAnywhere
                    text: root.previewDetail(previewPane.item, previewPane.info)
                }
            }

            Text {
                anchors.centerIn: parent
                width: parent.width - 48
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
                color: root.muted
                font.pixelSize: 13
                visible: feagle.visibleResults.length === 0
                text: {
                    if (feagle.query.length > 0)
                        return "No matches for “" + feagle.query + "”";
                    if (feagle.filter === "files")
                        return "Type to search files";
                    return "Type to search your files and applications";
                }
            }
        }
    }
}
