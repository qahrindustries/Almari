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
from _harness import Checks, load   # noqa: E402

from gi.repository import Gio, GLib, Gtk

sw = load()
check = Checks()
sw.desktop_top_inset = lambda default=0.0, max_age=8.0: 0.0
sw.save_config = lambda c: None
sw.save_config_soon = lambda c, delay_ms=400: None
sw.save_progress = lambda s: None

cfg = sw.themed_config(sw.load_config())
app = sw.App(cfg, "window")
app.set_application_id("dev.umar.shelfwall.cardtest")
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
# What matters is the layout boxes shelfwall builds itself, which have no
# business intercepting anything.
OURS = (sw._Card, Gtk.Box)
STOCK = (Gtk.Switch, Gtk.ScrolledWindow, Gtk.Button, Gtk.SpinButton,
         Gtk.Scale, Gtk.DropDown, Gtk.ListView)


def capture_claimers(card):
    """Controllers on shelfwall's own containers that swallow presses early."""
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


def interactive(card):
    return [w for w in walk(card)
            if isinstance(w, (Gtk.Switch, Gtk.Button, Gtk.Scale,
                              Gtk.SpinButton, Gtk.DropDown))]


def hits_itself(card, widget):
    """Does a press at the widget's centre actually land on it?

    Controls scrolled out of the card's viewport are not a bug -- they are
    one flick of the wheel away -- so they are reported as unknown rather
    than dead.
    """
    ok, rect = widget.compute_bounds(card)
    if not ok or rect.size.width <= 0 or rect.size.height <= 0:
        return None
    cy = rect.origin.y + rect.size.height / 2
    if cy < 0 or cy > card.get_height():
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
            ok, rect = bc.compute_bounds(root)
            check("book card fits inside the window",
                  ok and rect.origin.x >= 0 and rect.origin.y >= 0
                  and rect.origin.x + rect.size.width <= root.get_width() + 1
                  and rect.origin.y + rect.size.height <= root.get_height() + 1,
                  (rect.origin.x, rect.origin.y, rect.size.width,
                   rect.size.height, root.get_width(), root.get_height())
                  if ok else None)
            a.hide_book_card()

        a.quit()
        return False

    GLib.timeout_add(500, probe)


app.connect("activate", on_activate)
app.run([])
check.done()
