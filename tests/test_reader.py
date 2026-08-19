import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import Checks, library, load   # noqa: E402

from gi.repository import GLib, Gtk

sw = load()
check = Checks()

# A book from the command line if one is named, and a generated one
# otherwise: paging exactness needs a real layout, not a real purchase.
if len(sys.argv) > 1:
    EPUB = os.path.abspath(sys.argv[1])
else:
    root = library()
    found = sorted(os.path.join(d, f) for d, _, fs in os.walk(root)
                   for f in fs if f.endswith(".epub"))
    EPUB = found[0]
title, author, _cover, _name = sw.read_epub(EPUB)
book = {"path": EPUB, "title": title, "author": author, "cover": None,
        "color": [0.4, 0.2, 0.2], "size": os.path.getsize(EPUB)}
cfg = dict(sw.DEFAULTS)


app = Gtk.Application(application_id="dev.umar.almari.test")

def run(a):
    win = Gtk.ApplicationWindow(application=a)
    win.set_default_size(1200, 800)
    reader = sw.EpubReaderView(cfg)
    win.set_child(reader)
    win.present()

    def phase():
        reader.open(book)
        GLib.timeout_add(900, measure)
        return False

    def pump():
        ctx = GLib.MainContext.default()
        for _ in range(200):
            if not ctx.pending():
                break
            ctx.iteration(False)

    def display_line_at(window_y):
        """Buffer offset of the wrapped line drawn at `window_y`.

        Paragraph offsets are too coarse to prove anything here: a whole
        screen can sit inside one paragraph, so two different screens would
        report the same line.
        """
        tv = reader.textview
        _, by = tv.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, 0,
                                           int(window_y))
        return reader._display_line_at(by).get_offset()

    def next_display_offset(offset):
        tv = reader.textview
        it = reader.buffer.get_iter_at_offset(offset)
        return offset if not tv.forward_display_line(it) else it.get_offset()

    def line_top_at(offset):
        """Buffer offset of the wrapped line at the top of the screen."""
        adj = reader.scroller.get_vadjustment()
        adj.set_value(offset)
        pump()
        return display_line_at(0)

    def last_visible(offset):
        adj = reader.scroller.get_vadjustment()
        adj.set_value(offset)
        pump()
        return display_line_at(int(adj.get_page_size()) - 1)

    def measure():
        adj = reader.scroller.get_vadjustment()
        # pick a chapter with real scrollable length
        for ci in range(len(reader.chapters)):
            reader.show_chapter(ci)
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
            if adj.get_upper() - adj.get_page_size() > adj.get_page_size() * 5:
                break
        span = adj.get_upper() - adj.get_page_size()
        # Four page turns are measured below, and a chapter that runs out
        # part way through rolls into the next one -- which is correct
        # behaviour but says nothing about where a page turn lands.
        check("chapter is long enough to page through",
              span > adj.get_page_size() * 4, span)

        adj.set_value(0.0)
        seen_tops = []
        for step in range(4):
            before = adj.get_value()
            bottom_line = last_visible(before)
            adj.set_value(before)
            reader.page(1)
            after = adj.get_value()
            top_line = line_top_at(after)
            adj.set_value(after)
            seen_tops.append((before, after, bottom_line, top_line))
            check(f"page {step+1} advances", after > before, (before, after))
            # Carrying on exactly where the screen ran out means one of two
            # things, depending on where the fold fell: the line that was cut
            # in half at the bottom is now the top line, or -- if the last
            # line happened to end flush with the bottom edge -- the top line
            # is the one straight after it. Anything else skips text or
            # shows it twice.
            allowed = (bottom_line, next_display_offset(bottom_line))
            check(f"page {step+1} loses no line", top_line in allowed,
                  (top_line, allowed))

        # forward then back returns to a whole line, never past the start
        adj.set_value(seen_tops[-1][1])
        pump()
        reader.page(-1)
        back = adj.get_value()
        pump()
        check("page back moves up", back < seen_tops[-1][1], back)
        check("page back stays in range", back >= 0)

        # top edge always lands on a line boundary. The adjustment is set
        # again first: window coordinates only follow the adjustment once the
        # scrolled window has been through a layout pass.
        adj.set_value(back)
        pump()
        tv = reader.textview
        _, by = tv.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, 0, 0)
        # Measured in wrapped display lines, not paragraphs: a paragraph can
        # be a dozen lines tall, so paragraph alignment says nothing about
        # whether the top of the screen cuts a line of text in half.
        it = reader._display_line_at(by)
        line_top = tv.get_iter_location(it).y
        _, wy = tv.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, 0, line_top)
        check("page back lands on a whole line", abs(wy) <= 2, wy)

        # full-width margins
        reader.cfg["reader_full_width"] = True
        reader._relayout()
        check("full width margin is a hairline",
              reader.textview.get_left_margin() <= 16,
              reader.textview.get_left_margin())
        reader.cfg["reader_full_width"] = False
        reader._relayout()
        check("capped measure indents",
              reader.textview.get_left_margin() > 16,
              reader.textview.get_left_margin())
        reader.cfg["reader_full_width"] = True

        # justification resets both ways
        reader.cfg["reader_justify"] = False
        reader._relayout()
        check("justify off -> LEFT",
              reader.tag_body.get_property("justification") == Gtk.Justification.LEFT)
        reader.cfg["reader_justify"] = True
        reader._relayout()
        check("justify on -> FILL",
              reader.tag_body.get_property("justification") == Gtk.Justification.FILL)

        # chrome
        check("find bar hidden by default", not reader.find_bar.get_visible())
        reader.focus_search()
        check("find bar shows on demand", reader.find_bar.get_visible())
        reader.hide_search()
        check("find bar hides again", not reader.find_bar.get_visible())
        check("toc hidden by default", not reader.toc_pane.get_visible())
        reader.toggle_toc()
        check("toc toggles on", reader.toc_pane.get_visible())
        reader.toggle_toc()
        check("toc toggles off", not reader.toc_pane.get_visible())

        # progress record carries the percentage the info card shows
        reader.remember()
        rec = sw.load_progress().get(book["path"], {})
        check("progress records pct", "pct" in rec and "chapters" in rec, rec)

        a.quit()
        return False

    GLib.timeout_add(600, phase)

app.connect("activate", run)
app.run([])
check.done()
