# shelfwall

A bookshelf that *is* your desktop. shelfwall draws a wooden bookcase on the
Wayland background layer, stands your epub library on its shelves — spines,
covers, or a mix — and opens any book into a full reading view when you click
it. It is a wallpaper you can read.

Built for Hyprland with GTK4 and `wlr-layer-shell`. It integrates with
[illogical-impulse](https://github.com/end-4/dots-hyprland) / quickshell, but
runs perfectly well on its own.

![Shelves in front of the wallpaper](docs/shelf-wallpaper.jpg)

![Spines, with the last book on each shelf leaning into the gap](docs/shelf-spines.jpg)

## What it does

- **Reads your library.** Point it at a folder; every `.epub` under it becomes
  a book. Cover art, title, author and spine colour are taken from the file —
  spine colour from the cover, thickness from the file size.
- **Draws a real bookcase.** Planks, uprights, a top beam, shadows under each
  shelf, and books that lean into the gap at the end of a row.
- **Opens books in place.** Clicking a book turns the wallpaper into a reader:
  chapters, a table of contents, search, remembered position and a progress
  bar. No separate window, no application to alt-tab to.
- **Stays out of the way.** It lives below every window. Your desktop is only
  a bookshelf when nothing is covering it.

## Install

```sh
git clone https://github.com/<you>/shelfwall
cd shelfwall
./install.sh
```

Dependencies: `gtk4`, `gtk4-layer-shell`, `python-gobject`, `python-cairo`.
`install.sh` will fetch them with pacman on Arch; on other distributions
install the equivalents yourself and re-run it.

Then point it at your books and start it:

```sh
shelfwall --dir ~/Books --mode bg
```

To have it come back after a reboot, add it to your compositor's autostart:

```conf
# hyprland.conf
exec-once = ~/.local/bin/shelfwall --mode bg
```

Kill `hyprpaper` / `swww` first — they compete for the same layer.

## Using it

Everything happens on the shelf itself. There is no toolbar and no gear icon.

| Action | What it does |
| --- | --- |
| Click a book | Open it |
| Double-click anywhere | Settings |
| Drag a book | Rearrange the shelves |
| Right-click a book | Its details, progress, and how it should stand |
| `Esc`, or a click outside a card | Close the card |

A single click waits out the double-click interval before it opens anything,
so a double-click meant for the settings never opens a book on the way past.

### Reading

| Key | |
| --- | --- |
| `←` `→` / `Space` | Turn the page |
| `↑` `↓` / `j` `k` | Scroll a line |
| `n` `p` | Next / previous chapter |
| `t` | Contents |
| `/` or `Ctrl-F` | Find in book |
| `+` `−` `Ctrl-0` | Text size |
| `Home` `End` | Start / end of chapter |
| `Esc` | Close the find bar, then the contents, then the book |

Turning a page lands on exactly the line the last screen ran out on — the page
is advanced to a real line boundary rather than by a fixed fraction of the
viewport, so nothing is ever repeated or skipped.

Your position in every book is remembered in `~/.cache/shelfwall/progress.json`,
and the right-click card shows how far through each one you are.

### Per-book views

Right-clicking a book lets that one book ignore the shelf-wide display mode:

- **Auto** — follow the shelf setting
- **Cover** — stand face out
- **Spine** — stand spine out
- **Tilted** — lean, wherever it happens to sit

The choice is saved, so it survives a restart.

### The wall behind the shelves

The bookcase can stand against a flat colour or against your desktop
wallpaper. Wallpaper mode keeps the app exactly as it is and simply shows the
picture through the case, dimmed enough that spines stay readable.

## Settings

All of them live in the card you get by double-clicking, and all of them are
written straight to `~/.config/shelfwall/config.json`.

| Key | |
| --- | --- |
| `books_dir` | Scanned recursively for `.epub` |
| `display` | `spine`, `cover` or `mixed` |
| `face_out_every` | In `mixed`: every Nth book stands face out. `0` disables |
| `scale` | Book and shelf size |
| `sort` | `author`, `title`, `random` or `custom` (what dragging sets) |
| `book_order` | The dragged arrangement, as a list of paths |
| `book_states` | Per-book view overrides, keyed by path |
| `tilt_books` | Whether books lean at all |
| `tilt_angle` | How far, in degrees. Clamped to the gap that is actually there |
| `wall_mode` | `color` or `wallpaper` |
| `wallpaper_path`, `wallpaper_dim` | The picture, and how far it is darkened |
| `reader_full_width` | Full-bleed text, or a capped measure |
| `reader_width` | The measure, in characters, when not full width |
| `reader_font`, `reader_font_size`, `reader_line_height`, `reader_justify` | Typography |
| `theme_from_shell` | Follow illogical-impulse's generated Material palette |
| `layer_shell` | `bottom` (default), `background` or `top` |
| `open_cmd` | External reader, for "Open elsewhere" |

Colours (`wall`, `wood`, `reader_*`) are derived from the shell palette while
`theme_from_shell` is on, and are not written back to the config so that
retheming keeps working.

## Controlling it

`shelfwall ctl <command>` talks to the running instance over a socket. It
short-circuits before GTK is imported, so it is cheap enough to bind to a key.

```sh
shelfwall ctl toggle-reader          # open/close the last book read
shelfwall ctl open "Animal Farm"     # by title or path fragment
shelfwall ctl random
shelfwall ctl settings               # open the settings card
shelfwall ctl wallpaper toggle       # wallpaper behind the shelves
shelfwall ctl book-state tilt "Animal Farm"
shelfwall ctl set scale 1.4
shelfwall ctl get                    # the whole config, as JSON
shelfwall ctl state                  # what it is doing right now
shelfwall ctl rescan
shelfwall ctl quit
```

The library folder, the config file and the shell palette are all watched, so
adding a book or editing a setting takes effect without a restart.
`pkill -USR1 -f shelfwall` forces a rescan, `-USR2` a config reload.

## quickshell / illogical-impulse

`quickshell/` holds the integration. `services/Bookshelf.qml` starts and stops
shelfwall with the shell, exposes global shortcuts, and pushes the two things
only the shell knows: which wallpaper is current, and whether the shelves
should stand in front of it.

It deliberately does **not** mirror shelfwall's own settings. shelfwall's
`config.json` is the single owner of them, so a setting changed in the card
survives a login instead of being overwritten by the shell's stale copy.

Copy `services/Bookshelf.qml` into your quickshell config, and merge the two
snippets under `modules/` into `Config.qml` and the background settings page.

## Tests

```sh
python3 tests/test_shelf.py          # layout, tilt, hit-testing, drop targets
python3 tests/test_interaction.py    # click/double-click/drag, cards, state
python3 tests/test_wiring.py         # gestures and overlays, in a real window
python3 tests/test_reader.py BOOK.epub   # paging exactness, needs a real epub
```

They need GTK but not a compositor, apart from `test_reader.py`, which briefly
opens a window.

## Cache

`~/.cache/shelfwall/` holds extracted covers, the metadata index and reading
progress. Deleting it is safe; the covers are rebuilt on the next scan.

## Licence

MIT. See [LICENSE](LICENSE).
