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

# a leaning book rests on a neighbour: it never leans into open air, and the
# edge it leans on comes to rest flush against that neighbour, not inside it.
def fwd(it, px, py):
    """Where a point of the upright book actually lands once it has leaned."""
    a = math.radians(it["deg"])
    cx = it["x"] if it["deg"] < 0 else it["x"] + it["w"]
    cy = it["y_base"]
    dx, dy = px - cx, py - cy
    return (cx + dx * math.cos(a) - dy * math.sin(a) + it["shift"],
            cy + dx * math.sin(a) + dy * math.cos(a))

def row_of(it):
    return sorted((o for o in shelf._items if o["row"] == it["row"]),
                  key=lambda o: o["x"])

tilted = [it for it in shelf._items if it.get("deg", 0)]
check("last book on a part-filled shelf leans", len(tilted) >= 1,
      [round(i.get("deg", 0), 1) for i in shelf._items])

for it in tilted:
    row = row_of(it)
    k = row.index(it)
    left = row[k - 1] if k > 0 else None
    right = row[k + 1] if k + 1 < len(row) else None

    if it["deg"] < 0:
        check("leans left only with a book to rest on", left is not None)
        check("leans left only with nothing on its right", right is None)
        # top-left edge lands flush on the neighbour, never inside it
        tx, _ = fwd(it, it["x"], it["y"])
        check("left lean rests flush against the neighbour",
              abs(tx - it["x"]) < 0.6, (tx, it["x"]))
        check("left lean clears the neighbour",
              tx >= left["x"] + left["w"] - 0.6, (tx, left["x"] + left["w"]))
        # the base slides out into the empty end of the shelf
        bx, _ = fwd(it, it["x"], it["y_base"])
        check("left lean slides its base into the gap", bx > it["x"] + 1,
              (bx, it["x"]))
    else:
        check("leans right only onto something", right is not None)
        tx, _ = fwd(it, it["x"] + it["w"], it["y"])
        check("right lean rests flush against the neighbour",
              abs(tx - (it["x"] + it["w"])) < 0.6, (tx, it["x"] + it["w"]))
        bx, _ = fwd(it, it["x"], it["y_base"])
        check("right lean clears the book on its left",
              left is None or bx >= left["x"] + left["w"] - 0.6,
              (bx, left["x"] + left["w"] if left else None))

    # the leaned book is still pickable at the middle of where it now sits
    mx, my = fwd(it, it["x"] + it["w"] / 2, it["y"] + it["h"] / 2)
    check("leaning book is hit-testable where it sits",
          shelf.at(mx, my) is it["b"], it["b"]["title"])

# tilt off => nothing leans
cfg["tilt_books"] = False
shelf.invalidate(); shelf.top_inset = 0.0; shelf._ensure(1920, 1080)
check("tilt off keeps books upright", all(it.get("deg", 0) == 0 for it in shelf._items))

# per-book tilt state
cfg["tilt_books"] = True
cfg["book_states"] = {"/b/4.epub": "tilt"}
shelf.invalidate(); shelf.top_inset = 0.0; shelf._ensure(1920, 1080)
it4 = shelf._index["/b/4.epub"]
check("per-book tilt applied", it4["deg"] != 0, it4["deg"])
row4 = row_of(it4)
k4 = row4.index(it4)
if k4 + 1 < len(row4):
    right = row4[k4 + 1]
    check("mid-shelf tilt leans onto the book at its right", it4["deg"] > 0,
          it4["deg"])
    # leaning right, the book's own right edge is where it comes to rest, and
    # the reserved room is behind it, so the book to its right is untouched
    tx, _ = fwd(it4, it4["x"] + it4["w"], it4["y"])
    check("mid-shelf tilt does not reach its right neighbour",
          tx <= right["x"] + 0.6, (tx, right["x"]))
    bx, _ = fwd(it4, it4["x"], it4["y_base"])
    left4 = row4[k4 - 1] if k4 > 0 else None
    check("mid-shelf tilt does not reach its left neighbour",
          left4 is None or bx >= left4["x"] + left4["w"] - 0.6,
          (bx, left4["x"] + left4["w"] if left4 else None))

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
