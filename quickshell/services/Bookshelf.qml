pragma Singleton
pragma ComponentBehavior: Bound

import qs
import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Hyprland
import qs.modules.common

/**
 * Owns the shelfwall bookshelf wallpaper.
 *
 * shelfwall is a separate GTK4 process because it draws its shelves with
 * cairo, but it is not a separate *app*: it maps a layer-shell surface on the
 * background layer, underneath this shell's own background panel, and takes
 * all of its orders from here.
 *
 * What this service does *not* do is own shelfwall's settings. It used to
 * keep its own copy of every one -- book size, sort order, reading measure,
 * font -- and push the whole set down a second after launch. Anything the
 * user changed in shelfwall's own settings card was therefore overwritten by
 * this shell's stale copy on the next login, which is what made settings look
 * like they never stuck. shelfwall's config.json is now the only home for
 * them, and this service pushes exactly the two keys the shell alone knows:
 * which wallpaper is current, and whether the shelves should stand in front
 * of it.
 */
Singleton {
    id: root

    readonly property var opts: Config.options?.background?.bookshelf ?? null
    readonly property bool enabled: (Config.ready ?? false) && (opts?.enable ?? false)
    readonly property string command: opts?.command ?? "shelfwall"
    readonly property bool wallpaperBehind: opts?.wallpaperBehind ?? false
    readonly property string wallpaperPath: Config.options?.background?.wallpaperPath ?? ""

    // While the shelf is up the shell's own wallpaper would cover it and its
    // panel would swallow every click, so both step aside. That holds in
    // wallpaper mode too: there the shelf draws the picture itself, behind
    // its own shelves.
    readonly property bool coveringWallpaper: root.enabled
    readonly property bool wantsPointer: root.enabled && !GlobalStates.overlayOpen && !GlobalStates.screenLocked

    property bool readerOpen: false

    /**
     * The bar is deliberately left alone. The shelf and the reader are the
     * same surface at the same size, so hiding the bar for one of them made
     * the whole desktop jump on every open and close.
     */
    function setReaderOpen(open) {
        root.readerOpen = open;
    }

    signal started
    signal stopped

    function ctl(args) {
        Quickshell.execDetached(["bash", "-lc", `${root.command} ctl ${args} >/dev/null 2>&1`]);
    }

    function start() {
        // `ctl ping` doubles as the "already running?" check, so a restart of
        // the shell never leaves two shelves stacked on the same layer.
        Quickshell.execDetached(["bash", "-lc", `${root.command} ctl ping >/dev/null 2>&1 || ` + `(setsid ${root.command} --mode bg >/dev/null 2>&1 &)`]);
        root.started();
        pushTimer.restart();
    }

    function stop() {
        root.setReaderOpen(false);
        Quickshell.execDetached(["bash", "-lc", `${root.command} ctl quit >/dev/null 2>&1`]);
        root.stopped();
    }

    function restart() {
        stop();
        restartTimer.restart();
    }

    function toggle() {
        if (!root.opts)
            return;
        root.opts.enable = !root.opts.enable;
    }

    function toggleReader() {
        if (!root.enabled) {
            root.opts.enable = true;
            readerAfterStart.restart();
            return;
        }
        ctl("toggle-reader");
    }

    function closeReader() {
        ctl("close-reader");
    }

    function rescan() {
        ctl("rescan");
    }

    /** Open shelfwall's own settings card -- the one place its settings live. */
    function openSettings() {
        if (!root.enabled) {
            root.opts.enable = true;
            settingsAfterStart.restart();
            return;
        }
        ctl("settings");
    }

    /**
     * Shelves in front of the wallpaper, or in front of a flat colour. The
     * app itself stays exactly where it is either way; only the wall behind
     * the bookcase changes.
     */
    function toggleWallpaperBehind() {
        if (!root.opts)
            return;
        if (!root.opts.enable) {
            root.opts.enable = true;
        }
        root.opts.wallpaperBehind = !root.opts.wallpaperBehind;
    }

    /** Push the two settings the shell owns, and only those. */
    function pushShellOwned() {
        if (!root.enabled)
            return;
        const path = String(root.wallpaperPath).replace(/'/g, "");
        const mode = root.wallpaperBehind ? "wallpaper" : "color";
        Quickshell.execDetached(["bash", "-lc", `${root.command} ctl set wallpaper_path '${path}'; ` + `${root.command} ctl set wall_mode ${mode} >/dev/null 2>&1`]);
    }

    onEnabledChanged: {
        if (root.enabled)
            root.start();
        else
            root.stop();
    }
    onWallpaperBehindChanged: pushDebounce.restart()
    onWallpaperPathChanged: pushDebounce.restart()

    Timer {
        id: pushTimer
        interval: 900 // let the process claim its socket first
        onTriggered: root.pushShellOwned()
    }
    Timer {
        id: restartTimer
        interval: 400
        onTriggered: root.start()
    }
    Timer {
        id: readerAfterStart
        interval: 1400
        onTriggered: root.ctl("toggle-reader")
    }
    Timer {
        id: settingsAfterStart
        interval: 1400
        onTriggered: root.ctl("settings")
    }
    Timer {
        // Coalesce a wallpaper change and a mode flip into one push.
        id: pushDebounce
        interval: 250
        onTriggered: root.pushShellOwned()
    }

    function load() {
        if (root.enabled)
            root.start();
    }

    GlobalShortcut {
        name: "bookshelfToggle"
        description: "Toggle the bookshelf wallpaper"
        onPressed: root.toggle()
    }
    GlobalShortcut {
        name: "bookshelfWallpaperBehind"
        description: "Toggle the wallpaper behind the bookshelf"
        onPressed: root.toggleWallpaperBehind()
    }
    GlobalShortcut {
        name: "bookshelfSettings"
        description: "Open the bookshelf settings"
        onPressed: root.openSettings()
    }
    GlobalShortcut {
        name: "bookshelfReaderToggle"
        description: "Open/close the bookshelf reader"
        onPressed: root.toggleReader()
    }
    GlobalShortcut {
        name: "bookshelfReaderClose"
        description: "Close the bookshelf reader"
        onPressed: root.closeReader()
    }
    GlobalShortcut {
        name: "bookshelfRescan"
        description: "Rescan the bookshelf for new books"
        onPressed: root.rescan()
    }

    IpcHandler {
        target: "bookshelf"

        function toggle(): void {
            root.toggle();
        }
        function reader(): void {
            root.toggleReader();
        }
        function closeReader(): void {
            root.closeReader();
        }
        function rescan(): void {
            root.rescan();
        }
        function settings(): void {
            root.openSettings();
        }
        function wallpaperBehind(): void {
            root.toggleWallpaperBehind();
        }
        // shelfwall reports its own reader state. The bar no longer moves for
        // it, but the shell still tracks whether a book is open.
        function readerOpened(): void {
            root.setReaderOpen(true);
        }
        function readerClosed(): void {
            root.setReaderOpen(false);
        }
        function restart(): void {
            root.restart();
        }
        function send(command: string): void {
            root.ctl(command);
        }
    }
}
