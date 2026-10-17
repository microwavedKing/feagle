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

    function secondaryLabel(item) {
        return item.kind === "app" ? "Application" : (item.snippet || "");
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
        if (feagle.results.length === 0)
            return;
        root.currentIndex = Math.max(
            0, Math.min(root.currentIndex + delta, feagle.results.length - 1));
    }

    function openLauncher() {
        root.open = true;
        root.currentIndex = 0;
        root.daemonSlow = false;
        field.text = "";
        feagle.reset();
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
        width: Math.min(620, parent.width - 48)
        height: Math.min(440, parent.height - 96)
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
                        root.currentIndex = Math.max(0, feagle.results.length - 1);
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
            visible: feagle.results.length > 0
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
                    const count = feagle.results.length;
                    const kind = feagle.mode === "hybrid" ? "semantic" : "keyword";
                    return count + (count === 1 ? " result" : " results")
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
                text: "↑↓  ↵ open  esc close"
            }
        }

        Rectangle {
            id: dividerBottom
            anchors { bottom: footer.top; left: parent.left; right: parent.right }
            height: 1
            color: root.border
            visible: feagle.results.length > 0
        }

        ListView {
            id: list
            anchors.top: dividerTop.bottom
            anchors.bottom: dividerBottom.top
            anchors.left: parent.left
            anchors.right: parent.right
            clip: true
            model: feagle.results
            currentIndex: root.currentIndex
            onCurrentIndexChanged: {
                if (currentIndex >= 0)
                    positionViewAtIndex(currentIndex, ListView.Contain);
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

                IconImage {
                    id: rowIcon
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    anchors.leftMargin: 18
                    implicitSize: 22
                    source: row.modelData.kind === "app"
                        ? Quickshell.iconPath(row.modelData.icon || "", "application-x-executable")
                        : Quickshell.iconPath(root.fileIcon(row.modelData.path), "text-x-generic")
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

        Text {
            anchors.centerIn: list
            width: list.width - 48
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
            color: root.muted
            font.pixelSize: 13
            visible: feagle.results.length === 0
            text: feagle.query.length === 0
                ? "Type to search your files and applications"
                : "No matches for “" + feagle.query + "”"
        }
    }
}
