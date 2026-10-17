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
// `visibleResults` applies the `filter` ("all" / "apps" / "files") client-side,
// and `requestPreview()` fetches a small preview of the selected file. After a
// launch the component reports it back to the daemon (`op: "used"`) so the
// daemon can rank frequently used applications higher.
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
    /// Which results to expose through `visibleResults`: "all", "apps" or "files".
    property string filter: "all"
    /// Wait this many milliseconds before fetching a preview for a new selection.
    property int previewDebounceMs: 80
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
    /// The preview of the selected file: `{kind: "text"|"image"|"meta", ...}` or null.
    property var preview: null
    /// Last preview error, or "".
    property string previewError: ""
    /// Whether the socket is connected.
    readonly property bool connected: socket.connected

    /// `results` filtered by `filter`; this is what a launcher should display.
    readonly property var visibleResults: {
        if (root.filter === "apps")
            return root.results.filter((item) => item.kind === "app");
        if (root.filter === "files")
            return root.results.filter((item) => item.kind === "file");
        return root.results;
    }

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
            req: "launcher",
            query: root.query,
            mode: modeName,
            apps_limit: root.appsLimitFor()
        };
        if (root.limit > 0)
            msg.limit = root.limit;
        root.busy = true;
        send(msg);
    }

    /// How many applications the next request should ask for.
    function appsLimitFor(): int {
        if (root.filter === "files")
            return 0;   // the user only wants files: do not rank applications
        return root.query.trim().length === 0 ? root.browseAppsLimit : root.appsLimit;
    }

    /// Switch the client-side result filter: "all", "apps" or "files".
    /// The visible list updates at once; the next request asks for less work.
    function setFilter(name): void {
        root.filter = name;
    }

    /// Cycle the filter forward (or back with `reverse`).
    function cycleFilter(reverse): void {
        const order = ["all", "apps", "files"];
        const at = Math.max(0, order.indexOf(root.filter));
        root.filter = order[(at + (reverse ? order.length - 1 : 1)) % order.length];
    }

    /// Fetch a preview for `item` (files only); debounced, safe to call on hover.
    function requestPreview(item): void {
        pendingPreview = item && item.kind === "file" ? item : null;
        if (pendingPreview)
            previewDebounce.restart();
        else {
            previewDebounce.stop();
            root.preview = null;
            root.previewError = "";
        }
    }

    /// Run the result at `index` of `visibleResults`.
    function activate(index): void {
        if (index < 0 || index >= root.visibleResults.length)
            return;
        activateItem(root.visibleResults[index]);
    }

    /// Run a single result item.
    function activateItem(item): void {
        if (!item)
            return;
        if (item.kind === "app") {
            Quickshell.execDetached(item.exec);
            // Tell the daemon so it can rank this application higher next time.
            if (item.id)
                send({ op: "used", key: item.id });
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
    /// The file awaiting a preview request (set by `requestPreview`).
    property var pendingPreview: null

    function send(obj): void {
        if (!socket.connected)
            return;
        socket.write(JSON.stringify(obj) + "\n");
        socket.flush();
    }

    function sendPreview(): void {
        const item = root.pendingPreview;
        if (!item)
            return;
        var msg = { op: "preview", req: "preview", path: item.path };
        if (item.line !== null && item.line !== undefined)
            msg.line = item.line;
        send(msg);
    }

    function applyPreview(msg): void {
        if (msg.ok) {
            root.preview = msg;
            root.previewError = "";
        } else {
            root.preview = null;
            root.previewError = msg.error || "preview error";
        }
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
        if (msg && msg.req === "preview") {
            applyPreview(msg);
            return;   // previews never affect the result list or `busy`
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

    property Timer previewDebounce: Timer {
        interval: root.previewDebounceMs
        repeat: false
        onTriggered: root.sendPreview()
    }
}
