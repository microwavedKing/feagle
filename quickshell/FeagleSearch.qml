// FeagleSearch — reusable feagle backend for a Quickshell launcher.
//
// It owns the connection to the `feagle queryd` daemon and turns a query into a
// ready-to-render list of results (applications first, then file hits). Drop one
// into your own launcher, bind the query, read `results`, and call `activate()`:
//
//   import Quickshell
//   import "path/to/feagle/quickshell" as Feagle
//
//   Feagle.FeagleSearch { id: feagle; active: launcherOpen }
//
// Typing is two-phase so it never waits for the model: `setQuery()` fires an
// instant keyword (`fts`) request and, after `debounceMs`, a semantic (`hybrid`)
// one that replaces the list. While `active` it also sends `warmup` so
// `llama-server` loads in the background.
//
// See README.md in this directory for the wire protocol and a full launcher.
pragma ComponentBehavior: Bound

import QtQuick
import Quickshell
import Quickshell.Io

QtObject {
    id: root

    // ---- configuration -----------------------------------------------------
    /// The queryd socket. Defaults to the daemon's standard location.
    property string socketPath:
        (Quickshell.env("XDG_RUNTIME_DIR") || "") + "/feagle/queryd.sock"
    /// Connect (and warm the model up) while true; disconnect while false.
    property bool active: false
    /// Wait this many milliseconds after typing before the semantic pass.
    property int debounceMs: 120
    /// How many applications to show above the file hits while typing.
    property int appsLimit: 8
    /// How many applications to list when the query is empty (browse mode).
    property int browseAppsLimit: 300
    /// Maximum file hits; 0 uses the daemon's configured limit.
    property int limit: 0
    /// The `feagle` command used to open files (a name on PATH or an absolute path).
    /// Defaults to `$FEAGLE_BIN`, then `"feagle"`.
    property string feagleCommand: Quickshell.env("FEAGLE_BIN") || "feagle"

    // ---- state (read-only for the launcher) --------------------------------
    /// The current query text.
    property string query: ""
    /// Mixed results: `{kind: "app" | "file", ...}`.
    property var results: []
    /// Mode of the last reply: "fts" (keyword) or "hybrid" (semantic).
    property string mode: ""
    /// Duration of the last search, in milliseconds.
    property real tookMs: 0
    /// True while a reply is pending.
    property bool busy: false
    /// Last daemon error, or "".
    property string error: ""
    /// Whether the socket is connected.
    readonly property bool connected: socket.connected

    /// Emitted after `activate()` runs something, with the item it ran.
    signal activated(var item)

    // ---- api ---------------------------------------------------------------
    /// Ask the daemon to start loading the model in the background.
    function warmup(): void {
        send({ op: "warmup" });
    }

    /// Clear the query and results (call when the launcher opens).
    function reset(): void {
        root.query = "";
        root.results = [];
        root.mode = "";
        root.tookMs = 0;
        root.busy = false;
        root.error = "";
        if (root.active) {
            root.warmup();
            root.request("fts");
        }
    }

    /// Set the query: an instant keyword pass, a semantic pass after the debounce.
    function setQuery(text): void {
        root.query = text;
        root.request("fts");
        if (text.trim().length > 0)
            debounce.restart();
        else
            debounce.stop();   // nothing to embed for an empty query
    }

    /// Run a launcher request in the given mode ("fts" or "hybrid").
    function request(modeName): void {
        // An empty query means "browse": list every application, not just a few.
        const browsing = root.query.trim().length === 0;
        var msg = {
            op: "launcher",
            query: root.query,
            mode: modeName,
            apps_limit: browsing ? root.browseAppsLimit : root.appsLimit
        };
        if (root.limit > 0)
            msg.limit = root.limit;
        root.busy = true;
        send(msg);
    }

    /// Run the result at `index` (launch an application or open a file).
    function activate(index): void {
        if (index < 0 || index >= root.results.length)
            return;
        activateItem(root.results[index]);
    }

    /// Run a single result item.
    function activateItem(item): void {
        if (!item)
            return;
        if (item.kind === "app") {
            Quickshell.execDetached(item.exec);
        } else {
            const argv = [root.feagleCommand, "open", item.path];
            if (item.time !== null && item.time !== undefined)
                argv.push("--time", String(item.time));
            else if (item.page !== null && item.page !== undefined)
                argv.push("--page", String(item.page));
            else if (item.line !== null && item.line !== undefined)
                argv.push("--line", String(item.line));
            Quickshell.execDetached(argv);
        }
        root.activated(item);
    }

    // ---- internals ---------------------------------------------------------
    function send(obj): void {
        if (!socket.connected)
            return;
        socket.write(JSON.stringify(obj) + "\n");
        socket.flush();
    }

    function handleLine(line): void {
        if (!line)
            return;
        var msg;
        try {
            msg = JSON.parse(line);
        } catch (e) {
            return;
        }
        root.busy = false;
        if (msg && msg.ok) {
            root.results = msg.results || [];
            root.mode = msg.mode || "";
            root.tookMs = msg.took_ms || 0;
            root.error = "";
        } else if (msg) {
            root.error = msg.error || "queryd error";
        }
    }

    property Socket socket: Socket {
        path: root.socketPath
        connected: root.active
        onConnectedChanged: {
            if (connected && root.active) {
                root.warmup();
                root.request("fts");
            }
        }
        parser: SplitParser {
            onRead: (line) => root.handleLine(line)
        }
    }

    property Timer debounce: Timer {
        interval: root.debounceMs
        repeat: false
        onTriggered: root.request("hybrid")
    }
}
