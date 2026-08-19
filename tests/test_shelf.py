import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import Checks, load   # noqa: E402

import math
import time

import cairo

sw = load()
check = Checks()

def mkbooks(n, cover=True):
    return [{"path": f"/b/{i}.epub", "title": f"Book {i}", "author": "A",
             "cover": ("/tmp/none.png" if cover else None),
             "color": [0.4, 0.2, 0.2], "size": 900_000 + i * 50_000}
            for i in range(n)]


# ---- stable seed
check("stable_seed deterministic", sw.stable_seed("/a/b.epub") == sw.stable_seed("/a/b.epub"))
check("stable_seed differs", sw.stable_seed("/a") != sw.stable_seed("/b"))

# ---- custom sort
books = mkbooks(5)
order = ["/b/3.epub", "/b/1.epub"]
out = sw.sort_books(list(books), "custom", order)
check("custom order honoured", [b["path"] for b in out][:2] == order, [b["path"] for b in out])
check("unlisted books trail", [b["path"] for b in out][2:] == ["/b/0.epub", "/b/2.epub", "/b/4.epub"], [b["path"] for b in out])

# ---- human helpers
check("human_size", sw.human_size(1536) == "1.5 KB", sw.human_size(1536))
check("human_ago never", sw.human_ago(0) == "never")
check("human_ago hours", "hour" in sw.human_ago(int(time.time()) - 7200), sw.human_ago(int(time.time()) - 7200))

# ---- shelf layout / tilt / hit test
cfg = dict(sw.DEFAULTS)
cfg["books_dir"] = "/b"
cfg["display"] = "spine"
cfg["scale"] = 1.0
cfg["tilt_books"] = True
sw.desktop_top_inset = lambda default=0.0, max_age=8.0: 0.0
shelf = sw.Shelf(cfg, mkbooks(12, cover=False))
shelf.top_inset = 0.0
shelf._ensure(1920, 1080)
check("items built", len(shelf._items) == 12, len(shelf._items))

# every book must be hit-testable at its own centre
missed = [it["b"]["path"] for it in shelf._items
          if shelf.at(it["x"] + it["w"] / 2, it["y"] + it["h"] / 2) is not it["b"]]
check("all books hit-testable", not missed, missed)

# tilted book: centre of the rotated quad must hit, and it must not overlap
tilted = [it for it in shelf._items if it.get("deg", 0)]
check("last book leans", len(tilted) >= 1, [round(it.get("deg",0),1) for it in shelf._items])
for it in tilted:
    a = math.radians(it["deg"])
    cx, cy = it["x"] + it["w"], it["y_base"]
    # top-left corner of the book after the lean
    dx, dy = it["x"] - cx, it["y"] - cy
    tx = cx + dx * math.cos(a) - dy * math.sin(a)
    check("lean moves top rightwards", tx > it["x"], (tx, it["x"]))
    check("lean hit at rotated centre",
          shelf.at(cx + (dx/2) * math.cos(a) - (dy/2) * math.sin(a),
                   cy + (dx/2) * math.sin(a) + (dy/2) * math.cos(a)) is it["b"])
    # nothing to the left may be swept through
    row = [o for o in shelf._items if o["row"] == it["row"] and o is not it]
    if row:
        left = max(row, key=lambda o: o["x"])
        check("lean clears left neighbour", tx >= left["x"] + left["w"] - 1,
              (tx, left["x"] + left["w"]))

# tilt off => nothing leans
cfg["tilt_books"] = False
shelf.invalidate(); shelf.top_inset = 0.0; shelf._ensure(1920, 1080)
check("tilt off keeps books upright", all(it.get("deg", 0) == 0 for it in shelf._items))

# per-book tilt state
cfg["tilt_books"] = True
cfg["book_states"] = {"/b/4.epub": "tilt"}
shelf.invalidate(); shelf.top_inset = 0.0; shelf._ensure(1920, 1080)
it4 = shelf._index["/b/4.epub"]
check("per-book tilt applied", it4["deg"] > 0, it4["deg"])
nxt = [o for o in shelf._items if o["row"] == it4["row"] and o["x"] > it4["x"]]
if nxt:
    right = min(nxt, key=lambda o: o["x"])
    sweep = it4["x"] + it4["w"] + it4["h"] * math.sin(math.radians(it4["deg"]))
    check("mid-shelf tilt reserves its sweep", sweep <= right["x"] + 1, (sweep, right["x"]))

# per-book cover state
cfg["book_states"] = {"/b/2.epub": "spine"}
cfg["display"] = "cover"
shelf.invalidate(); shelf.top_inset = 0.0; shelf._ensure(1920, 1080)
check("per-book spine overrides display", shelf._index["/b/2.epub"]["kind"] == "spine")

# ---- drop insertion
cfg["book_states"] = {}; cfg["display"] = "spine"
shelf.invalidate(); shelf.top_inset = 0.0; shelf._ensure(1920, 1080)
first = min(shelf._items, key=lambda i: (i["row"], i["x"]))
idx, mark = shelf._insertion(first["x"] + 1, first["y"] + first["h"] / 2)
check("drop before first book", idx == first["index"], idx)
last = max(shelf._items, key=lambda i: (i["row"], i["x"]))
idx2, _ = shelf._insertion(last["x"] + last["w"] + 40, last["y"] + 5)
check("drop after last book", idx2 == last["index"] + 1, idx2)

# ---- draw must not raise, with and without a wallpaper
surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1920, 1080)
shelf.draw(None, cairo.Context(surf), 1920, 1080)
shelf.drag_path = shelf._items[0]["b"]["path"]
shelf.drag_xy = (500, 500)
shelf.set_drop(500, 500)
shelf.draw(None, cairo.Context(surf), 1920, 1080)
check("draw with drag in flight", True)

cfg["wall_mode"] = "wallpaper"; cfg["wallpaper_path"] = "/does/not/exist.png"
shelf.invalidate(); shelf.top_inset = 0.0
shelf.draw(None, cairo.Context(surf), 1920, 1080)
check("missing wallpaper falls back", True)

check.done()
