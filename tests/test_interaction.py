import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import Checks, load   # noqa: E402

import time

from gi.repository import GLib, Gtk

sw = load()
check = Checks()


def pump(ms=0):
    ctx = GLib.MainContext.default()
    end = time.monotonic() + ms / 1000.0
    while True:
        while ctx.pending():
            ctx.iteration(False)
        if time.monotonic() >= end:
            break
        time.sleep(0.01)

def mkbooks(n):
    return [{"path": f"/b/{i}.epub", "title": f"Book {i}", "author": "A",
             "cover": None, "color": [0.4, 0.2, 0.2], "size": 900_000}
            for i in range(n)]

cfg = dict(sw.DEFAULTS)
cfg["books_dir"] = "/b"
sw.desktop_top_inset = lambda default=0.0, max_age=8.0: 0.0
sw.save_config = lambda c: None          # keep the test off the real config
sw.save_progress = lambda s: None

app = sw.App(cfg, "window")
app.books = mkbooks(8)
shelf = sw.Shelf(cfg, app.books)
shelf.top_inset = 0.0
shelf._ensure(1600, 900)
app.shelves = [shelf]
app.areas = []

opened, settings = [], []
app.open_book = lambda b: opened.append(b["path"])
app.toggle_settings = lambda: settings.append(1)
app.redraw = lambda: None

first = min(shelf._items, key=lambda i: (i["row"], i["x"]))
second = sorted([i for i in shelf._items if i["row"] == first["row"]],
                key=lambda i: i["x"])[1]
bx = first["x"] + first["w"] / 2
by = first["y"] + first["h"] / 2

# ---- single click on a book opens it, but only after the double-click window
opened.clear(); settings.clear()
app.on_press(None, 1, bx, by, shelf)
app.on_drag_begin(None, bx, by, shelf, None)
app.on_drag_end(None, 0, 0, shelf, None)
check("single click does not open immediately", opened == [], opened)
pump(700)
check("single click opens after the wait", opened == [first["b"]["path"]], opened)
check("single click opens no settings", settings == [], settings)

# ---- double click opens settings and never opens the book
opened.clear(); settings.clear()
app.on_press(None, 1, bx, by, shelf)
app.on_drag_begin(None, bx, by, shelf, None)
app.on_drag_end(None, 0, 0, shelf, None)
app.on_press(None, 2, bx, by, shelf)       # second click lands inside the window
pump(700)
check("double click on a book opens settings", settings == [1], settings)
check("double click on a book opens no book", opened == [], opened)

# ---- double click on bare shelf also opens settings
opened.clear(); settings.clear()
empty_x = 1600 - 30
app.on_press(None, 1, empty_x, by, shelf)
app.on_drag_begin(None, empty_x, by, shelf, None)
app.on_drag_end(None, 0, 0, shelf, None)
app.on_press(None, 2, empty_x, by, shelf)
pump(700)
check("double click off a book opens settings", settings == [1], settings)
check("double click off a book opens no book", opened == [], opened)

# ---- single click on bare shelf does nothing
opened.clear(); settings.clear()
app.on_press(None, 1, empty_x, by, shelf)
app.on_drag_begin(None, empty_x, by, shelf, None)
app.on_drag_end(None, 0, 0, shelf, None)
pump(700)
check("single click off a book is inert", opened == [] and settings == [],
      (opened, settings))

# ---- drag past the threshold rearranges instead of opening
opened.clear(); settings.clear()
order_before = [b["path"] for b in app.books]
target_x = second["x"] + second["w"] + 5
app.on_press(None, 1, bx, by, shelf)
app.on_drag_begin(None, bx, by, shelf, None)
class FakeArea:
    def queue_draw(self): pass
    def get_width(self): return 1600
    def get_height(self): return 900
area = FakeArea()
app.on_drag_update(None, 2, 0, shelf, area)          # under the threshold
check("small movement is not a drag", shelf.drag_path is None)
app.on_drag_update(None, target_x - bx, 0, shelf, area)
check("past the threshold picks the book up",
      shelf.drag_path == first["b"]["path"], shelf.drag_path)
app.on_drag_end(None, target_x - bx, 0, shelf, area)
pump(700)
check("dragging opens nothing", opened == [], opened)
order_after = [b["path"] for b in app.books]
check("drag reorders the shelf", order_after != order_before,
      (order_before[:4], order_after[:4]))
check("drag switches to the custom order", cfg["sort"] == "custom", cfg["sort"])
check("drag records the order", cfg["book_order"] == order_after)
check("drag state is cleared", shelf.drag_path is None and shelf.drop_mark is None)
check("no book was lost", sorted(order_after) == sorted(order_before))

# ---- right click never opens a book
opened.clear(); settings.clear()
shown = []
app.show_book_card = lambda b, x, y, a: shown.append(b["path"])
class FakeGesture:
    def set_state(self, s): pass
app.on_secondary(FakeGesture(), 1, bx, by, shelf, area)
pump(700)
check("right click opens the info card", len(shown) == 1, shown)
check("right click opens no book", opened == [], opened)

# ---- per-book state round trip
book = app.books[0]
app.invalidate_shelves = lambda: None
app.set_book_state(book, "tilt")
check("book state stored", cfg["book_states"].get(book["path"]) == "tilt")
app.set_book_state(book, "auto")
check("auto clears the override", book["path"] not in cfg["book_states"])

# ---- reset arrangement
cfg["book_states"] = {"/b/1.epub": "cover"}
app.reset_arrangement()
check("reset clears the order", cfg["book_order"] == [])
check("reset clears per-book views", cfg["book_states"] == {})
check("reset leaves custom sort", cfg["sort"] == "author", cfg["sort"])

# ---- cards build in every permutation
class StubApp:
    def __init__(self): self.books = mkbooks(3); self.reader = None
    def reader_open(self): return False
    def hide_settings(self): pass
    def hide_book_card(self): pass
    def invalidate_shelves(self): pass
    def resort(self): pass
    def rescan(self): pass
    def apply_reader_settings(self): pass
    def reset_arrangement(self): pass
    def set_book_state(self, b, s): pass
    def open_from_card(self): pass
    def open_external(self, b): pass
    def forget_progress(self, b): pass

stub = StubApp()
built = 0
for tilt in (True, False):
    for wall in ("color", "wallpaper"):
        for full in (True, False):
            c = dict(sw.DEFAULTS)
            c.update({"tilt_books": tilt, "wall_mode": wall,
                      "reader_full_width": full})
            card = sw.SettingsCard(c, stub)
            card.rebuild()
            built += 1
check("settings card builds in every permutation", built == 8, built)

info = sw.BookInfoCard(dict(sw.DEFAULTS), stub)
b0 = mkbooks(1)[0]
# Placed near a corner, and then in one, purely to prove show_for survives
# both -- where it actually lands is not assertable here. This card has no
# window, so it is never laid out, and an unlaid-out scrolled box of
# wrapping labels does not measure to a stable size: ask it twice and it
# answers 545 and then 1486. Whether the card stays on screen is checked in
# test_cards.py, at five pointer positions inside a real window, against
# the allocation the card actually receives.
info.show_for(b0, 1500, 850, 1600, 900)
check("info card places itself near a corner",
      info.get_margin_start() >= 12 and info.get_margin_top() >= 12,
      (info.get_margin_start(), info.get_margin_top()))
b0["cover"] = "/tmp/x.png"
info.show_for(b0, 10, 10, 1600, 900)
check("info card builds for a book with a cover", True)

check.done()
