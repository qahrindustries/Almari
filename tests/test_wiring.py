import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import Checks, library, load   # noqa: E402

from gi.repository import Gdk, Gio, GLib, Gtk

sw = load()
check = Checks()
sw.desktop_top_inset = lambda default=0.0, max_age=8.0: 0.0
sw.save_config = lambda c: None
cfg = sw.themed_config(sw.load_config())
# The app rescans on its own once it is up, so the library has to be the
# one in the config -- setting a.books by hand is undone a moment later.
cfg["books_dir"] = library()
app = sw.App(cfg, "window")
# The running Almari already owns the real application id, so a
# second instance would just wake that one and exit.
app.set_application_id("dev.umar.almari.wiringtest")
app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)

def on_activate(a):
    a.books = sw.sort_books(sw.build_index(cfg["books_dir"]), "author", None)
    win = a.make_window(monitor=None, primary=True)

    def probe():
        area = a.areas[0]
        ctrls = []
        c = area.observe_controllers()
        for i in range(c.get_n_items()):
            ctrls.append(c.get_item(i))
        kinds = [type(x).__name__ for x in ctrls]
        check("drawing area has a click gesture", "GestureClick" in kinds, kinds)
        check("drawing area has a drag gesture", "GestureDrag" in kinds, kinds)
        check("drawing area has motion tracking",
              "EventControllerMotion" in kinds, kinds)
        clicks = [x for x in ctrls if isinstance(x, Gtk.GestureClick)]
        buttons = sorted(x.get_button() for x in clicks)
        check("one primary and one secondary click gesture",
              buttons == [Gdk.BUTTON_PRIMARY, Gdk.BUTTON_SECONDARY], buttons)
        primary = [x for x in clicks if x.get_button() == Gdk.BUTTON_PRIMARY][0]
        check("primary click runs before the drag gesture",
              primary.get_propagation_phase() == Gtk.PropagationPhase.CAPTURE,
              primary.get_propagation_phase())
        drag = [x for x in ctrls if isinstance(x, Gtk.GestureDrag)][0]
        check("drag gesture is on the left button",
              drag.get_button() == Gdk.BUTTON_PRIMARY, drag.get_button())

        check("scrim built", a.scrim is not None)
        check("scrim starts hidden", not a.scrim.get_visible())
        check("settings card built", a.settings_card is not None)
        check("book card built", a.book_card is not None)
        check("reader built", a.reader is not None)

        # scrim follows whatever card is open
        a.show_settings()
        check("scrim appears with the settings card", a.scrim.get_visible())
        check("settings card visible", a.settings_card.get_visible())
        a.dismiss_cards()
        check("clicking outside hides the card", not a.settings_card.get_visible())
        check("scrim goes with it", not a.scrim.get_visible())

        a.show_book_card(a.books[0], 100, 100, area)
        check("scrim appears with the book card", a.scrim.get_visible())
        check("book card visible", a.book_card.get_visible())
        a.dismiss_cards()
        check("book card dismissed", not a.book_card.get_visible())

        # opening settings while the book card is up swaps them, never stacks
        a.show_book_card(a.books[0], 100, 100, area)
        a.show_settings()
        check("settings replaces the book card", not a.book_card.get_visible())
        a.dismiss_cards()

        # no close buttons left anywhere
        def buttons_in(widget, found):
            ch = widget.get_first_child()
            while ch:
                if isinstance(ch, Gtk.Button) and ch.get_label() in ("✕", "✕"):
                    found.append(ch.get_label())
                buttons_in(ch, found)
                ch = ch.get_next_sibling()
            return found
        check("settings card has no close button",
              buttons_in(a.settings_card, []) == [])
        a.book_card.show_for(a.books[0], 10, 10, 1200, 800)
        check("book card has no close button", buttons_in(a.book_card, []) == [])
        a.hide_book_card()
        check("reader has no close button", buttons_in(a.reader, []) == [])

        # Running the command again activates this instance rather than
        # starting another; it must not build a second shelf on top of the
        # first.
        windows = len(a.areas)
        a.do_activate()
        check("a second activation adds no windows", len(a.areas) == windows,
              (windows, len(a.areas)))

        a.quit()
        return False

    GLib.timeout_add(800, probe)

app.connect("activate", on_activate)
app.run([])
check.done()
