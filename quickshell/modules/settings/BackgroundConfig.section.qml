// The Almari section of illogical-impulse's Background settings page.
// Paste this ContentSection into modules/settings/BackgroundConfig.qml.
    ContentSection {
        icon: "auto_stories"
        title: Translation.tr("Almari bookshelf")

        // Everything about how the shelf looks and reads -- book size, order,
        // spines or covers, per-book views, the reading measure and font --
        // lives in Almari's own settings card, opened by double-clicking
        // the shelf or with the button below. It is kept there rather than
        // mirrored here so that one place owns it and the setting a user picks
        // survives a reboot.
        ConfigRow {
            Layout.fillWidth: true
            ConfigSwitch {
                Layout.fillWidth: false
                buttonIcon: "shelves"
                text: Translation.tr("Enable")
                checked: Config.options.background.almari.enable
                onCheckedChanged: {
                    Config.options.background.almari.enable = checked;
                }
            }
            Item {
                Layout.fillWidth: true
            }
            DialogButton {
                buttonText: Translation.tr("Rescan library")
                enabled: Config.options.background.almari.enable
                onClicked: Almari.rescan()
            }
            DialogButton {
                buttonText: Translation.tr("Restart")
                enabled: Config.options.background.almari.enable
                onClicked: Almari.restart()
            }
        }

        ConfigSwitch {
            visible: Config.options.background.almari.enable
            buttonIcon: "wallpaper"
            text: Translation.tr("Show the wallpaper behind the shelves")
            checked: Config.options.background.almari.wallpaperBehind
            onCheckedChanged: {
                Config.options.background.almari.wallpaperBehind = checked;
            }
            StyledToolTip {
                text: Translation.tr("Keeps the shelves exactly as they are and puts your wallpaper behind them instead of a flat color.")
            }
        }

        ContentSubsection {
            visible: Config.options.background.almari.enable
            title: Translation.tr("Everything else")
            tooltip: Translation.tr("Book size, order, spines or covers, leaning, per-book views, and the reader")

            DialogButton {
                buttonText: Translation.tr("Open Almari settings")
                onClicked: Almari.openSettings()
            }

            StyledText {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                color: Appearance.colors.colSubtext
                font.pixelSize: Appearance.font.pixelSize.smaller
                text: Translation.tr("On the shelf: click a book to read it, double-click anywhere for settings, drag a book to rearrange, right-click a book for its details and how it should stand.")
            }
        }
    }
