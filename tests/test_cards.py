"""The floating cards must be usable with the mouse.

A card that renders correctly can still be completely dead to the pointer:
if anything on the card's own subtree claims presses in the capture phase,
GTK hands the press to that claimant instead of the switch or button the
user aimed at, and every control silently stops responding while remaining
reachable by keyboard. That failure is invisible to a rendering test, so it
gets its own file.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import Checks, library, load   # noqa: E402

from gi.repository import Gio, GLib, Gtk

sw = load()
check = Checks()
sw.desktop_top_inset = lambda default=0.0, max_age=8.0: 0.0
sw.save_config = lambda c: None
sw.save_config_soon = lambda c, delay_ms=400: None
sw.save_progress = lambda s: None

cfg = sw.themed_config(sw.load_config())
# The app rescans on its own once it is up, so the library has to be the
# one in the config -- setting a.books by hand is undone a moment later.
cfg["books_dir"] = library()
app = sw.App(cfg, "window")
app.set_application_id("dev.umar.almari.cardtest")
app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)


def pump(ms=300):
    ctx = GLib.MainContext.default()
    end = time.monotonic() + ms / 1000.0
    while True:
        while ctx.pending():
            ctx.iteration(False)
        if time.monotonic() >= end:
            return
        time.sleep(0.01)


def controllers(widget):
    out, lst = [], widget.observe_controllers()
    for i in range(lst.get_n_items()):
        out.append(lst.get_item(i))
    return out


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child:
        yield from walk(child)
        child = child.get_next_sibling()


# Stock widgets carry capture gestures of their own (a Switch pans, a
# ScrolledWindow kinetically drags); those are internal and well-behaved.
# What matters is the layout boxes Almari builds itself, which have no
# business intercepting anything.
OURS = (sw._Card, Gtk.Box)
STOCK = (Gtk.Switch, Gtk.ScrolledWindow, Gtk.Button, Gtk.SpinButton,
         Gtk.Scale, Gtk.DropDown, Gtk.ListView)


def capture_claimers(card):
    """Controllers on Almari's own containers that swallow presses early."""
    bad = []
    for w in walk(card):
        if not isinstance(w, OURS) or isinstance(w, STOCK):
            continue
        if w.get_first_child() is None:
            continue
        for c in controllers(w):
            if isinstance(c, Gtk.Gesture) and \
                    c.get_propagation_phase() == Gtk.PropagationPhase.CAPTURE:
                bad.append((type(w).__name__, type(c).__name__))
    return bad


CONTROLS = (Gtk.Switch, Gtk.Button, Gtk.Scale, Gtk.SpinButton, Gtk.DropDown)


def interactive(card):
    """The controls the card puts on screen, not their internal parts.

    A SpinButton is built out of two Buttons and a DropDown out of one more;
    those are the widget's own business and a press aimed at the middle of
    one may legitimately be handled by its owner.
    """
    out = []
    for w in walk(card):
        if not isinstance(w, CONTROLS):
            continue
        # A greyed-out control -- "Cover", for a book with no cover art --
        # is deliberately unclickable.
        if not w.get_sensitive():
            continue
        p = w.get_parent()
        while p is not None and p is not card:
            if isinstance(p, CONTROLS):
                break
            p = p.get_parent()
        else:
            out.append(w)
    return out


def hits_itself(card, widget):
    """Does a press at the widget's centre actually land on it?

    Controls scrolled out of the card's viewport are not a bug -- they are
    one flick of the wheel away -- so they are reported as unknown rather
    than dead.
    """
    ok, rect = widget.compute_bounds(card)
    if not ok or rect.size.width <= 0 or rect.size.height <= 0:
        return None
    if rect.origin.y < 0 or \
            rect.origin.y + rect.size.height > card.get_height():
        return None
    picked = card.pick(rect.origin.x + rect.size.width / 2,
                       rect.origin.y + rect.size.height / 2,
                       Gtk.PickFlags.DEFAULT)
    while picked is not None:
        if picked is widget:
            return True
        picked = picked.get_parent()
    return False


def on_activate(a):
    a.books = sw.sort_books(sw.build_index(cfg["books_dir"]), "author", None)
    a.make_window(monitor=None, primary=True)

    def probe():
        a.show_settings()
        pump(400)

        sc = a.settings_card
        bad = capture_claimers(sc)
        check("settings card has no press-swallowing capture gesture",
              bad == [], bad)

        controls = interactive(sc)
        check("settings card has controls to click", len(controls) > 4,
              len(controls))
        dead = [type(w).__name__ for w in controls
                if hits_itself(sc, w) is False]
        check("every settings control receives its own clicks",
              dead == [], dead)

        root = sc.get_root()
        ok, rect = sc.compute_bounds(root)
        check("settings card fits inside the window",
              ok and rect.size.height <= root.get_height(),
              (rect.size.height if ok else None, root.get_height()))

        a.hide_settings()
        if a.books:
            a.show_book_card(a.books[0], 20, 20, a.areas[0])
            pump(400)
            bc = a.book_card
            bad = capture_claimers(bc)
            check("book card has no press-swallowing capture gesture",
                  bad == [], bad)
            bcontrols = interactive(bc)
            check("book card has controls to click", len(bcontrols) >= 3,
                  len(bcontrols))
            dead = [type(w).__name__ for w in bcontrols
                    if hits_itself(bc, w) is False]
            check("every book card control receives its own clicks",
                  dead == [], dead)
            # Right-clicking near an edge must not push the card over it.
            area = a.areas[0]
            W, H = area.get_width(), area.get_height()
            for name, (px, py) in (("top left", (4, 4)),
                                   ("top right", (W - 4, 4)),
                                   ("bottom left", (4, H - 4)),
                                   ("bottom right", (W - 4, H - 4)),
                                   ("dead centre", (W // 2, H // 2))):
                a.hide_book_card()
                a.show_book_card(a.books[0], px, py, area)
                # The card corrects its own position on the first frame
                # after it is laid out; wait for that rather than for a
                # fixed number of milliseconds.
                deadline = time.monotonic() + 3.0
                while bc._tick is not None and time.monotonic() < deadline:
                    pump(50)
                check(f"book card settles at {name}", bc._tick is None)
                ok, r = bc.compute_bounds(root)
                inside = (ok and r.origin.x >= -1 and r.origin.y >= -1
                          and r.origin.x + r.size.width <= W + 1
                          and r.origin.y + r.size.height <= H + 1)
                check(f"book card stays on screen at {name}", inside,
                      (r.origin.x, r.origin.y, r.size.width, r.size.height,
                       W, H) if ok else None)
                dx = max(r.origin.x - px, px - (r.origin.x + r.size.width), 0)
                dy = max(r.origin.y - py, py - (r.origin.y + r.size.height), 0)
                check(f"book card opens next to the pointer at {name}",
                      ok and max(dx, dy) <= 40,
                      (dx, dy, r.origin.x, r.origin.y, r.size.width,
                       r.size.height, px, py) if ok else None)
            a.hide_book_card()

        a.quit()
        return False

    GLib.timeout_add(500, probe)


app.connect("activate", on_activate)
app.run([])
check.done()
