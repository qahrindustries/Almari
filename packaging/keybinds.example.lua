-- Almari keybinds for illogical-impulse's Hyprland config.
-- Copy into ~/.config/hypr/custom/keybinds.lua.
--
-- SUPER+ALT is used as the prefix because it is free in a stock
-- illogical-impulse install. Change it to taste.
--
-- The shelf toggles go through quickshell's GlobalShortcuts so the shell
-- stays the single owner of the state. The reading keys talk to Almari's
-- control socket directly: the reader lives on the wallpaper layer behind
-- every window, so a compositor-level bind is what lets you turn a page
-- while another window holds focus.

local almari = "$HOME/.local/bin/almari"

--##! Almari
hl.bind("SUPER + ALT + B", hl.dsp.global("quickshell:almariToggle"),
    { description = "Almari: Toggle the shelf wallpaper" })
hl.bind("SUPER + ALT + W", hl.dsp.global("quickshell:almariWallpaperBehind"),
    { description = "Almari: Wallpaper behind the shelves" })
hl.bind("SUPER + ALT + S", hl.dsp.global("quickshell:almariSettings"),
    { description = "Almari: Open settings" })
hl.bind("SUPER + ALT + O", hl.dsp.global("quickshell:almariReaderToggle"),
    { description = "Almari: Open/close the reader" })
hl.bind("SUPER + ALT + Escape", hl.dsp.global("quickshell:almariReaderClose"),
    { description = "Almari: Close the reader" })
hl.bind("SUPER + ALT + F5", hl.dsp.global("quickshell:almariRescan"),
    { description = "Almari: Rescan for new books" })

hl.bind("SUPER + ALT + Right", hl.dsp.exec_cmd(almari .. " ctl page-down"),
    { description = "Almari: Next page", repeating = true })
hl.bind("SUPER + ALT + Left", hl.dsp.exec_cmd(almari .. " ctl page-up"),
    { description = "Almari: Previous page", repeating = true })
hl.bind("SUPER + ALT + Down", hl.dsp.exec_cmd(almari .. " ctl next-chapter"),
    { description = "Almari: Next chapter" })
hl.bind("SUPER + ALT + Up", hl.dsp.exec_cmd(almari .. " ctl prev-chapter"),
    { description = "Almari: Previous chapter" })
hl.bind("SUPER + ALT + C", hl.dsp.exec_cmd(almari .. " ctl toc"),
    { description = "Almari: Toggle contents" })
hl.bind("SUPER + ALT + Slash", hl.dsp.exec_cmd(almari .. " ctl find"),
    { description = "Almari: Find in book" })
