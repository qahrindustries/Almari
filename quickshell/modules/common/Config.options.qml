// The Almari options block for illogical-impulse's Config.qml, inside
// `property JsonObject background: JsonObject { ... }`.
                // Almari: an interactive epub bookshelf
                // drawn on the background layer, underneath this panel.
                //
                // Only what the shell itself owns lives here. Book size, sort
                // order, display mode, reading measure and font are Almari's
                // own settings, kept in ~/.config/almari/config.json and
                // edited by double-clicking the shelf. Mirroring them here as
                // well meant this shell pushed its stale copy over the user's
                // choices on every login.
                property JsonObject almari: JsonObject {
                    property bool enable: false
                    property string command: "almari"
                    // Show the desktop wallpaper behind the shelves instead of
                    // a flat colour. The shelf app itself stays exactly as it
                    // is; only the wall behind the bookcase changes.
                    property bool wallpaperBehind: false
                }
