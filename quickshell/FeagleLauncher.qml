// feagle launcher popup for Quickshell (example).
//
// Talks line-delimited JSON to feagle's queryd socket. The reply is a ready
// mixed list (applications first, then file hits), so this file only draws and
// launches.
//
// Two phases, so typing never waits for the model:
//   1. fast  — {"op":"launcher",...,"mode":"fts"}    on every keystroke
//   2. slow  — {"op":"launcher",...,"mode":"hybrid"} after a short debounce
// On open it also sends {"op":"warmup"} to warm llama-server in the background.
//
// Toggle it from a compositor hotkey with:  qs ipc call feagle-launcher toggle
import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

PanelWindow {
    id: root

    property bool open: false
    property string query: ""
    property var results: []
    property int currentIndex: 0

    // queryd socket lives in the user runtime dir.
    readonly property string socketPath:
        (Quickshell.env("XDG_RUNTIME_DIR") || "/run/user") + "/feagle/queryd.sock"

    visible: open
    color: "transparent"
    exclusiveZone: 0
    anchors { top: true; bottom: true; left: true; right: true }
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.namespace: "feagle-launcher"
    WlrLayershell.keyboardFocus:
        root.open ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None

    function send(obj) {
        if (!socket.connected) return;
        socket.write(JSON.stringify(obj) + "\n");
        socket.flush();
    }

    function refresh(mode) {
        send({ op: "launcher", query: root.query, mode: mode, apps_limit: 8 });
    }

    function openLauncher() {
        root.open = true;
        root.query = "";
        field.text = "";
        root.results = [];
        root.currentIndex = 0;
        if (socket.connected) {
            send({ op: "warmup" });
            refresh("fts");
        }
        field.forceActiveFocus();
    }

    function closeLauncher() {
        root.open = false;
    }

    function activate(index) {
        if (index < 0 || index >= root.results.length) return;
        const item = root.results[index];
        if (item.kind === "app") {
            Quickshell.execDetached(item.exec);
        } else {
            const argv = ["feagle", "open", item.path];
            if (item.time !== null && item.time !== undefined)
                argv.push("--time", String(item.time));
            else if (item.page !== null && item.page !== undefined)
                argv.push("--page", String(item.page));
            else if (item.line !== null && item.line !== undefined)
                argv.push("--line", String(item.line));
            Quickshell.execDetached(argv);
        }
        closeLauncher();
    }

    function handleLine(line) {
        if (!line) return;
        let msg;
        try { msg = JSON.parse(line); } catch (e) { return; }
        if (msg && msg.ok && msg.results) {
            root.results = msg.results;
            root.currentIndex = 0;
        }
    }

    IpcHandler {
        target: "feagle-launcher"
        function toggle(): void { root.open ? root.closeLauncher() : root.openLauncher(); }
        function show(): void { root.openLauncher(); }
        function hide(): void { root.closeLauncher(); }
    }

    Socket {
        id: socket
        path: root.socketPath
        connected: root.open
        onConnectedChanged: {
            if (connected && root.open) {
                root.send({ op: "warmup" });
                root.refresh("fts");
            }
        }
        parser: SplitParser {
            onRead: (line) => root.handleLine(line)
        }
    }

    // Dimmed backdrop; a click outside the panel closes the launcher.
    Rectangle {
        anchors.fill: parent
        color: "#aa000000"
        MouseArea { anchors.fill: parent; onClicked: root.closeLauncher() }
    }

    Rectangle {
        id: panel
        width: Math.min(680, parent.width - 80)
        height: Math.min(480, parent.height - 120)
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: parent.height * 0.18
        radius: 12
        color: "#1e1e2e"
        border.color: "#45475a"

        Column {
            anchors.fill: parent
            anchors.margins: 12
            spacing: 8

            Rectangle {
                id: fieldBox
                width: parent.width
                height: 40
                radius: 8
                color: "#313244"

                TextInput {
                    id: field
                    anchors.fill: parent
                    anchors.margins: 10
                    color: "#cdd6f4"
                    font.pixelSize: 16
                    clip: true
                    onTextChanged: {
                        root.query = text;
                        root.refresh("fts");   // fast phase
                        debounce.restart();    // semantic phase after a pause
                    }
                    Keys.onPressed: (event) => {
                        if (event.key === Qt.Key_Down) {
                            root.currentIndex = Math.min(root.currentIndex + 1, root.results.length - 1);
                            event.accepted = true;
                        } else if (event.key === Qt.Key_Up) {
                            root.currentIndex = Math.max(root.currentIndex - 1, 0);
                            event.accepted = true;
                        } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                            root.activate(root.currentIndex);
                            event.accepted = true;
                        } else if (event.key === Qt.Key_Escape) {
                            root.closeLauncher();
                            event.accepted = true;
                        }
                    }
                }
            }

            Timer {
                id: debounce
                interval: 120
                repeat: false
                onTriggered: root.refresh("hybrid")
            }

            ListView {
                id: list
                width: parent.width
                height: parent.height - fieldBox.height - 8
                clip: true
                model: root.results
                currentIndex: root.currentIndex
                delegate: Rectangle {
                    required property var modelData
                    required property int index
                    width: list.width
                    height: 44
                    radius: 6
                    color: index === root.currentIndex ? "#45475a" : "transparent"
                    Column {
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: parent.left
                        anchors.leftMargin: 10
                        anchors.right: parent.right
                        anchors.rightMargin: 10
                        Text {
                            width: parent.width
                            color: "#cdd6f4"
                            elide: Text.ElideMiddle
                            text: {
                                if (modelData.kind === "app") return modelData.name;
                                let base = modelData.path;
                                if (modelData.time !== null && modelData.time !== undefined)
                                    base += "  @ " + modelData.time + "s";
                                else if (modelData.page !== null && modelData.page !== undefined)
                                    base += "  (page " + modelData.page + ")";
                                else if (modelData.line !== null && modelData.line !== undefined)
                                    base += ":" + modelData.line;
                                return base;
                            }
                        }
                        Text {
                            width: parent.width
                            color: "#a6adc8"
                            font.pixelSize: 11
                            elide: Text.ElideRight
                            visible: text.length > 0
                            text: modelData.snippet !== undefined ? modelData.snippet : ""
                        }
                    }
                    MouseArea {
                        anchors.fill: parent
                        onClicked: root.activate(index)
                    }
                }
            }
        }
    }
}
