#!/usr/bin/env python3
"""shelfwall - realistic epub bookshelf for Hyprland (GTK4 + layer-shell)."""

import os
import sys

# `shelfwall ctl <cmd>` is a keybind target, so it short-circuits before GTK
# is imported at all -- it only needs a socket, and a keystroke should not pay
# a toolkit startup to send one line.
if len(sys.argv) > 2 and sys.argv[1] == "ctl":
    import socket as _socket

    def _ctl(command):
        base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/shelfwall-{os.getuid()}"
        sig = os.environ.get("WAYLAND_DISPLAY", "wayland-0")
        path = os.path.join(base, f"shelfwall-{sig}.sock")
        try:
            s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
            s.settimeout(1.0)
            s.connect(path)
            s.sendall((command.strip() + "\n").encode())
            # Read to end of line or end of stream. A fixed 256-byte read
            # silently truncated the replies that are worth having -- `get`
            # returns the whole config, which outgrew it long ago.
            chunks = []
            try:
                while True:
                    part = s.recv(4096)
                    if not part:
                        break
                    chunks.append(part)
                    if part.endswith(b"\n"):
                        break
            except Exception:
                pass
            reply = b"".join(chunks).decode(errors="replace").strip()
            s.close()
            if reply:
                print(reply)
            return True
        except Exception as e:
            print(f"shelfwall: not running ({e})", file=sys.stderr)
            return False

    sys.exit(0 if _ctl(" ".join(sys.argv[2:])) else 1)

# gtk4-layer-shell works by interposing libwayland-client symbols, which only
# takes effect if it wins the symbol lookup against libwayland-client. GTK
# pulls in libwayland-client the moment Gdk is imported, so the interposing
# library has to be in the global symbol scope before that import happens.
#
# Loading it here with RTLD_GLOBAL is what does that. LD_PRELOADing
# liblayer-shell-preload.so -- the approach this used to take -- silently fails
# against current libwayland: the window falls back to an ordinary toplevel,
# which is why the "wallpaper" used to map as a small always-on-top app window.
# The library itself, not the preload shim, is what must be loaded first.
def _load_layer_shell_symbols():
    import ctypes
    for soname in ("libgtk4-layer-shell.so.0", "libgtk4-layer-shell.so",
                   "/usr/lib/libgtk4-layer-shell.so.0"):
        try:
            ctypes.CDLL(soname, mode=ctypes.RTLD_GLOBAL)
            return True
        except OSError:
            continue
    print("shelfwall: libgtk4-layer-shell not found; --mode bg will not work",
          file=sys.stderr)
    return False


_LAYER_SHELL_OK = _load_layer_shell_symbols()

import argparse
import colorsys
import hashlib
import html as html_module
import json
import math
import random
import subprocess
import time
import zipfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, Gio, GLib, Pango, PangoCairo  # noqa
import cairo  # noqa

APP_ID = "dev.umar.shelfwall"
CACHE = Path(GLib.get_user_cache_dir()) / "shelfwall"
CONF = Path(GLib.get_user_config_dir()) / "shelfwall" / "config.json"

CONFIG_VERSION = 2

DEFAULTS = {
    "books_dir": "~/Books",
    "scale": 1.0,
    "sort": "author",          # author | title | random | custom
    "face_out_every": 9,       # 0 disables face-out covers
    "display": "mixed",        # spine | cover | mixed
    "wall": [0.085, 0.078, 0.072],
    "wood": [0.36, 0.24, 0.145],
    "open_cmd": "xdg-open",
    "theme_from_shell": True,   # follow illogical-impulse material colors
    "layer_shell": "bottom",    # bottom | background | top

    # Books lean into the gap at the end of a shelf. Off keeps every book
    # perfectly upright.
    "tilt_books": True,
    "tilt_angle": 9.0,          # degrees; the lean is clamped to fit its gap

    # The wall behind the shelves: a flat color, or the desktop wallpaper.
    "wall_mode": "color",       # color | wallpaper
    "wallpaper_path": "",
    "wallpaper_dim": 0.45,      # how far the wallpaper is darkened, 0-1

    # Per-book overrides, keyed by absolute path:
    #   "auto" (follow the shelf-wide display mode) | "cover" | "spine" | "tilt"
    "book_states": {},
    # Absolute paths in the order the user dragged them into. Only consulted
    # when sort is "custom"; books missing from it keep their natural order
    # at the end.
    "book_order": [],

    # Bumped when a stored config needs rewriting rather than just topping up
    # with new keys. See migrate_config.
    "config_version": CONFIG_VERSION,
}

CLOTH = [
    (0.42, 0.13, 0.13), (0.13, 0.24, 0.35), (0.17, 0.31, 0.22),
    (0.36, 0.27, 0.10), (0.28, 0.15, 0.32), (0.50, 0.30, 0.12),
    (0.14, 0.16, 0.22), (0.45, 0.38, 0.24), (0.30, 0.10, 0.20),
]

DC = "{http://purl.org/dc/elements/1.1/}"
OPF = "{http://www.idpf.org/2007/opf}"
CNT = "{urn:oasis:names:tc:opendocument:xmlns:container}"


# ---------------------------------------------------------------- config

def load_config(cli_dir=None, cli_scale=None):
    cfg = dict(DEFAULTS)
    stored = None
    if CONF.exists():
        for attempt in range(3):
            try:
                stored = json.loads(CONF.read_text())
                break
            except Exception as e:
                # A concurrent writer, most likely. Losing the user's settings
                # to a torn read is much worse than waiting a moment.
                if attempt == 2:
                    print("bad config, keeping previous values:", e,
                          file=sys.stderr)
                else:
                    time.sleep(0.05)
        if stored:
            cfg.update(stored)
    if cli_dir:
        cfg["books_dir"] = cli_dir
    if cli_scale:
        cfg["scale"] = cli_scale
    cfg["books_dir"] = os.path.expanduser(cfg["books_dir"])
    # Migration: the grid layout was folded into the shelf as a display mode.
    legacy = cfg.pop("layout", None)
    if legacy == "grid" and cfg.get("display") == DEFAULTS["display"]:
        cfg["display"] = "cover"
    # A config written by an older build can carry the wrong type for the
    # collections, and every reader of them assumes they are usable.
    if not isinstance(cfg.get("book_states"), dict):
        cfg["book_states"] = {}
    if not isinstance(cfg.get("book_order"), list):
        cfg["book_order"] = []
    # The *stored* version decides what has to be migrated. Reading it off the
    # merged config would always see the current version, because DEFAULTS
    # supplies it whenever the file on disk does not.
    migrate_config(cfg, int((stored or {}).get("config_version", 0) or 0)
                   if stored else CONFIG_VERSION)
    return cfg


def migrate_config(cfg, was):
    """Bring a config written by an older build up to date.

    A changed default only reaches people who have never touched the setting,
    because a stored value always wins. Anything that has to change for
    everyone belongs here instead.
    """
    if was >= CONFIG_VERSION:
        return cfg
    if was < 2:
        # The shell bar used to be hidden for reading, and the setting that
        # did it is gone: the shelf and the reader are the same surface at the
        # same size, so dropping the bar for one of them made the whole
        # desktop jump on every open and close.
        cfg.pop("hide_shell_bar_in_reader", None)
    cfg["config_version"] = CONFIG_VERSION
    save_config(cfg)
    return cfg


# Colors the shell palette overwrites at runtime. Writing them back would
# freeze one theme snapshot into the config and break future retheming.
DERIVED_KEYS = ("wall", "wood", "reader_bg", "reader_fg", "reader_dim",
                "reader_accent")


# The text of the last config we wrote ourselves. The running instance
# watches its own config file so the shell can push settings into it, and
# without this every save would bounce straight back in as a reload.
_LAST_WRITTEN = {"text": None}


def save_config(cfg):
    out = dict(cfg)
    if out.get("theme_from_shell", True):
        for k in DERIVED_KEYS:
            out.pop(k, None)
    text = json.dumps(out, indent=2)
    try:
        CONF.parent.mkdir(parents=True, exist_ok=True)
        # Written atomically: the watcher on the other side of this file used
        # to be able to read a half-written file, fail to parse it and quietly
        # fall back to the defaults, which looked like settings not sticking.
        tmp = CONF.with_suffix(".json.tmp")
        tmp.write_text(text)
        os.replace(tmp, CONF)
        _LAST_WRITTEN["text"] = text
    except Exception as e:
        print("failed to save config:", e, file=sys.stderr)


# A Gtk.Scale fires value-changed for every step of a drag, and each one used
# to rewrite config.json -- a few hundred file writes to move one slider. The
# card asks for a save; the save happens once the user stops moving.
_PENDING_SAVE = {"source": None, "cfg": None}


def save_config_soon(cfg, delay_ms=400):
    _PENDING_SAVE["cfg"] = cfg
    if _PENDING_SAVE["source"] is not None:
        GLib.source_remove(_PENDING_SAVE["source"])
    _PENDING_SAVE["source"] = GLib.timeout_add(delay_ms, _flush_save)


def _flush_save():
    _PENDING_SAVE["source"] = None
    cfg = _PENDING_SAVE.pop("cfg", None)
    _PENDING_SAVE["cfg"] = None
    if cfg is not None:
        save_config(cfg)
    return False


def flush_pending_save():
    """Write out a debounced save now. For shutdown, where there is no later."""
    if _PENDING_SAVE["source"] is not None:
        GLib.source_remove(_PENDING_SAVE["source"])
        _flush_save()


def config_is_ours():
    """True when the config on disk is exactly what we last wrote."""
    try:
        return CONF.read_text() == _LAST_WRITTEN["text"]
    except Exception:
        return False


# ---------------------------------------------------------------- epub

def _opf_dir(p):
    d = os.path.dirname(p)
    return d + "/" if d else ""


def _opf_path(z):
    try:
        root = ET.fromstring(z.read("META-INF/container.xml"))
        return root.find(f".//{CNT}rootfile").get("full-path")
    except Exception:
        names = [n for n in z.namelist() if n.endswith(".opf")]
        if not names:
            raise ValueError("no opf")
        return names[0]


def read_epub(path):
    title, author, cover_bytes, cover_name = None, None, None, None
    with zipfile.ZipFile(path) as z:
        opf_path = _opf_path(z)
        opf = ET.fromstring(z.read(opf_path))
        base = _opf_dir(opf_path)

        for t in opf.iter(f"{DC}title"):
            if t.text:
                title = t.text.strip()
                break
        for a in opf.iter(f"{DC}creator"):
            if a.text:
                author = a.text.strip()
                break

        items = {}
        href_by_props = None
        for it in opf.iter(f"{OPF}item"):
            iid = it.get("id")
            href = it.get("href")
            props = it.get("properties") or ""
            if iid and href:
                items[iid] = href
            if "cover-image" in props:
                href_by_props = href

        cover_href = href_by_props
        if not cover_href:
            for m in opf.iter(f"{OPF}meta"):
                if (m.get("name") or "").lower() == "cover":
                    cover_href = items.get(m.get("content"))
                    break
        if not cover_href:
            for iid, href in items.items():
                if "cover" in iid.lower() and href.lower().endswith(
                        (".jpg", ".jpeg", ".png", ".webp")):
                    cover_href = href
                    break

        if cover_href:
            full = os.path.normpath(base + cover_href).replace("\\", "/")
            try:
                cover_bytes = z.read(full)
                cover_name = os.path.basename(full)
            except KeyError:
                cover_bytes = None

    if not title:
        title = Path(path).stem.replace("_", " ")
    return title, author or "", cover_bytes, cover_name


def _spine_hrefs(z, opf_path):
    root = ET.fromstring(z.read(opf_path))
    base = _opf_dir(opf_path)
    items = {it.get("id"): it.get("href")
             for it in root.iter(f"{OPF}item") if it.get("id")}
    hrefs = []
    spine = root.find(f"{OPF}spine")
    if spine is not None:
        for ref in spine.iter(f"{OPF}itemref"):
            href = items.get(ref.get("idref"))
            if href:
                hrefs.append(os.path.normpath(base + href).replace("\\", "/"))
    return hrefs


class _TextExtractor(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6",
              "tr", "blockquote"}
    SKIP = {"script", "style"}

    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    def text(self):
        raw = html_module.unescape("".join(self.parts))
        lines = [ln.strip() for ln in raw.splitlines()]
        out, blank = [], False
        for ln in lines:
            if ln:
                out.append(ln)
                blank = False
            elif not blank:
                out.append("")
                blank = True
        return "\n".join(out).strip()


def extract_epub_text(path, limit_chars=1_500_000):
    with zipfile.ZipFile(path) as z:
        opf_path = _opf_path(z)
        hrefs = _spine_hrefs(z, opf_path)
        if not hrefs:
            hrefs = sorted(n for n in z.namelist()
                            if n.lower().endswith((".xhtml", ".html", ".htm")))
        chunks, total = [], 0
        for href in hrefs:
            try:
                raw = z.read(href).decode("utf-8", errors="replace")
            except KeyError:
                continue
            ex = _TextExtractor()
            try:
                ex.feed(raw)
            except Exception:
                continue
            t = ex.text()
            if t:
                chunks.append(t)
                total += len(t)
                if total > limit_chars:
                    chunks.append("\n\n[...truncated...]")
                    break
        return "\n\n".join(chunks) if chunks else "(no readable text found)"


def dominant_color(img_path):
    try:
        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(img_path), 6, 6, False)
        px, n, stride = pb.get_pixels(), pb.get_n_channels(), pb.get_rowstride()
        r = g = b = c = 0
        for y in range(pb.get_height()):
            for x in range(pb.get_width()):
                o = y * stride + x * n
                r += px[o]; g += px[o + 1]; b += px[o + 2]; c += 1
        r, g, b = r / c / 255, g / c / 255, b / c / 255
        h, s, v = colorsys.rgb_to_hsv(r, g, b)
        s = min(1.0, max(s, 0.35))
        v = min(0.62, max(v, 0.22))
        return list(colorsys.hsv_to_rgb(h, s, v))
    except Exception:
        return None


def build_index(books_dir, force=False):
    CACHE.mkdir(parents=True, exist_ok=True)
    idx_file = CACHE / "index.json"
    old = {}
    if idx_file.exists() and not force:
        try:
            old = json.loads(idx_file.read_text())
        except Exception:
            old = {}

    books, new = [], {}
    root = Path(books_dir)
    if not root.exists():
        return books

    for p in sorted(root.rglob("*.epub")):
        key = str(p)
        mt = p.stat().st_mtime
        rec = old.get(key)
        if rec and abs(rec.get("mtime", 0) - mt) < 1 and \
                (not rec.get("cover") or Path(rec["cover"]).exists()):
            new[key] = rec
            books.append(dict(rec, path=key))
            continue
        try:
            title, author, cb, cname = read_epub(p)
        except Exception as e:
            print("skip", p.name, e, file=sys.stderr)
            continue
        cover = None
        if cb:
            h = hashlib.sha1(key.encode()).hexdigest()[:16]
            ext = os.path.splitext(cname or "")[1] or ".img"
            cp = CACHE / f"{h}{ext}"
            try:
                cp.write_bytes(cb)
                cover = str(cp)
            except Exception:
                cover = None
        color = dominant_color(cover) if cover else None
        if not color:
            color = list(CLOTH[int(hashlib.md5(title.encode()).hexdigest(), 16)
                               % len(CLOTH)])
        rec = {"mtime": mt, "title": title, "author": author,
               "cover": cover, "color": color, "size": p.stat().st_size}
        new[key] = rec
        books.append(dict(rec, path=key))

    try:
        idx_file.write_text(json.dumps(new))
    except Exception:
        pass
    return books


def sort_books(books, mode, order=None):
    """Order the shelf. `order` is the user's drag-arranged path list.

    Custom order is a list of paths rather than an index per book because the
    library is rescanned behind the user's back: indices go stale the moment a
    file is added or removed, paths do not.
    """
    if mode == "custom":
        rank = {p: i for i, p in enumerate(order or [])}
        # Books the user has never dragged sort after the arranged ones, in
        # author order, so a newly added epub appears at the end rather than
        # landing in an arbitrary spot in the middle of a curated shelf.
        tail = len(rank)
        books.sort(key=lambda b: (rank.get(b["path"], tail),
                                  (b["author"] or "zzz").lower(),
                                  b["title"].lower()))
    elif mode == "title":
        books.sort(key=lambda b: b["title"].lower())
    elif mode == "random":
        random.Random(1337).shuffle(books)
    else:
        books.sort(key=lambda b: ((b["author"] or "zzz").lower(),
                                  b["title"].lower()))
    return books


# ---------------------------------------------------------------- drawing

def shade(c, f):
    return [max(0.0, min(1.0, x * f)) for x in c]


def wood_plank(cr, x, y, w, h, base, seed):
    g = cairo.LinearGradient(0, y, 0, y + h)
    g.add_color_stop_rgb(0, *shade(base, 1.35))
    g.add_color_stop_rgb(0.18, *shade(base, 1.05))
    g.add_color_stop_rgb(1, *shade(base, 0.55))
    cr.set_source(g)
    cr.rectangle(x, y, w, h)
    cr.fill()

    rnd = random.Random(seed)
    cr.save()
    cr.rectangle(x, y, w, h)
    cr.clip()
    cr.set_line_width(1)
    for _ in range(int(w / 26)):
        yy = y + rnd.uniform(2, h - 2)
        amp = rnd.uniform(0.4, 1.6)
        cr.set_source_rgba(0, 0, 0, rnd.uniform(0.05, 0.14))
        cr.move_to(x, yy)
        step = 22
        xx = x
        while xx < x + w:
            cr.line_to(xx, yy + math.sin((xx + seed) / 55.0) * amp)
            xx += step
        cr.stroke()
    cr.restore()

    cr.set_source_rgba(1, 1, 1, 0.10)
    cr.rectangle(x, y, w, 1.5)
    cr.fill()
    cr.set_source_rgba(0, 0, 0, 0.45)
    cr.rectangle(x, y + h - 2, w, 2)
    cr.fill()


def draw_spine(cr, b, x, y_base, w, h, s, prog=0.0):
    col = b["color"]
    y_base = y_base - prog * 9 * s
    y = y_base - h
    g = cairo.LinearGradient(x, 0, x + w, 0)
    g.add_color_stop_rgb(0.0, *shade(col, 0.55))
    g.add_color_stop_rgb(0.10, *shade(col, 1.18 + 0.22 * prog))
    g.add_color_stop_rgb(0.72, *shade(col, 0.92))
    g.add_color_stop_rgb(1.0, *shade(col, 0.42))
    cr.set_source(g)
    r = 2.5 * s
    cr.new_path()
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.arc(x + w - r, y + r, r, 1.5 * math.pi, 2 * math.pi)
    cr.line_to(x + w, y_base)
    cr.line_to(x, y_base)
    cr.close_path()
    cr.fill()

    # head cap / page block hint
    cr.set_source_rgba(1, 1, 1, 0.06)
    cr.rectangle(x + 1, y + 1.5, w - 2, 2)
    cr.fill()

    lum = 0.299 * col[0] + 0.587 * col[1] + 0.114 * col[2]
    ink = (0.93, 0.87, 0.68) if lum < 0.5 else (0.12, 0.10, 0.08)

    rnd = random.Random(stable_seed(b["path"]) & 0xffff)
    if w > 26 * s and rnd.random() < 0.55:
        for yy in (y + h * 0.14, y + h * 0.30):
            cr.set_source_rgba(*ink, 0.5)
            cr.rectangle(x + 2, yy, w - 4, 1.2)
            cr.fill()

    pad = 12 * s
    fs = max(8.0, min(13.0, w * 0.30))
    layout = PangoCairo.create_layout(cr)
    layout.set_font_description(
        Pango.FontDescription(f"Inter {fs:.1f}"))
    layout.set_text(b["title"], -1)
    layout.set_width(int((h - 2 * pad) * Pango.SCALE))
    layout.set_ellipsize(Pango.EllipsizeMode.END)
    layout.set_single_paragraph_mode(True)

    cr.save()
    cr.rectangle(x, y, w, h)
    cr.clip()
    cr.translate(x + w / 2, y + pad)
    cr.rotate(math.pi / 2)
    _, lh = layout.get_pixel_size()
    cr.move_to(0, -lh / 2)
    cr.set_source_rgb(*ink)
    PangoCairo.show_layout(cr, layout)
    cr.restore()

    if b["author"] and h > 120 * s:
        al = PangoCairo.create_layout(cr)
        al.set_font_description(Pango.FontDescription(f"Inter {fs * 0.78:.1f}"))
        al.set_text(b["author"].split(",")[0], -1)
        al.set_width(int((h * 0.35) * Pango.SCALE))
        al.set_ellipsize(Pango.EllipsizeMode.END)
        cr.save()
        cr.rectangle(x, y, w, h)
        cr.clip()
        cr.translate(x + w / 2, y_base - pad)
        cr.rotate(math.pi / 2)
        _, alh = al.get_pixel_size()
        aw, _ = al.get_pixel_size()
        cr.move_to(-aw, -alh / 2)
        cr.set_source_rgba(*ink, 0.72)
        PangoCairo.show_layout(cr, al)
        cr.restore()

    cr.set_source_rgba(0, 0, 0, 0.30)
    cr.rectangle(x + w - 3 * s, y, 3 * s, h)
    cr.fill()

    if prog > 0.01:
        cr.set_source_rgba(1, 1, 1, 0.35 * prog)
        cr.set_line_width(1.5 * s)
        cr.rectangle(x + 0.75, y + 0.75, w - 1.5, h - 1.5)
        cr.stroke()


def draw_faceout(cr, b, pb, x, y_base, w, h, prog=0.0):
    y = y_base - h
    cr.set_source_rgba(0, 0, 0, 0.35)
    cr.rectangle(x + 3, y + 5, w, h)
    cr.fill()
    # Callers pass a cover already scaled to (w, h) and cached; rescaling a
    # multi-megapixel cover here is what used to burn a frame per book.
    if pb.get_width() != int(w) or pb.get_height() != int(h):
        pb = pb.scale_simple(max(1, int(w)), max(1, int(h)),
                             GdkPixbuf.InterpType.BILINEAR)
    Gdk.cairo_set_source_pixbuf(cr, pb, x, y)
    cr.rectangle(x, y, w, h)
    cr.fill()
    g = cairo.LinearGradient(x, 0, x + w, 0)
    g.add_color_stop_rgba(0, 1, 1, 1, 0.14)
    g.add_color_stop_rgba(0.25, 1, 1, 1, 0.0)
    g.add_color_stop_rgba(1, 0, 0, 0, 0.25)
    cr.set_source(g)
    cr.rectangle(x, y, w, h)
    cr.fill()
    if prog > 0.01:
        cr.set_source_rgba(1, 1, 1, 0.4 * prog)
        cr.set_line_width(2)
        cr.rectangle(x + 1, y + 1, w - 2, h - 2)
        cr.stroke()


def stable_seed(text):
    """A per-book random seed that survives a restart.

    Python randomises `str.__hash__` per process, so seeding the book jitter
    with `hash(path)` handed every book a new thickness and height on every
    launch: the shelf visibly reshuffled itself each time the machine was
    rebooted, which read as settings not sticking.
    """
    return int(hashlib.md5(text.encode("utf-8", "replace")).hexdigest()[:8], 16)


def _hypr_json(args):
    try:
        out = subprocess.run(["hyprctl", "-j"] + args, capture_output=True,
                             text=True, timeout=1.5)
        return json.loads(out.stdout)
    except Exception:
        return None


# Two `hyprctl` round trips answer this, and the shelf asks whenever any cache
# is dropped -- which is on every settings change. The bar does not move, so
# the answer is held briefly rather than re-shelled for each keystroke.
_INSET_CACHE = {"at": -1e9, "value": 0.0}


def desktop_top_inset(default=0.0, max_age=8.0):
    """Height of the shell's top bar plus two window gaps.

    The shelf is anchored flush to the left, right and bottom of the display;
    only the top is inset, so the bookcase clears the bar by the same margin a
    tiled window gets.
    """
    now = time.monotonic()
    if now - _INSET_CACHE["at"] < max_age:
        return _INSET_CACHE["value"]

    data = _hypr_json(["layers"])
    bar = 0.0
    if data:
        for _mon, v in data.items():
            for _lvl, entries in (v.get("levels") or {}).items():
                for e in entries:
                    ns = (e.get("namespace") or "").lower()
                    if "bar" in ns and float(e.get("y", 0)) <= 4:
                        bar = max(bar, float(e.get("y", 0)) + float(e.get("h", 0)))
    if bar <= 0:
        value = default
    else:
        gap = 0.0
        opt = _hypr_json(["getoption", "general:gaps_out"])
        if opt:
            raw = str(opt.get("custom") or opt.get("css") or "").strip()
            try:
                gap = float(raw.split()[0])
            except (ValueError, IndexError):
                gap = 0.0
        # 0.6 of the bar-plus-gaps figure: clearing the bar completely leaves a
        # band of bare wall that reads as a mistake rather than as breathing room.
        value = (bar + gap * 2) * 0.6

    _INSET_CACHE["at"] = now
    _INSET_CACHE["value"] = value
    return value


# One wallpaper, at one size, dimmed once. Rescaling a 4K photo takes long
# enough to be felt, and the wall is rebuilt on every settings change.
_WALLPAPER_CACHE = {"key": None, "surface": None}


def wallpaper_surface(path, W, H, dim):
    """The desktop wallpaper scaled to cover (W, H) and darkened, or None."""
    key = (path, int(W), int(H), round(float(dim), 3))
    if _WALLPAPER_CACHE["key"] == key:
        return _WALLPAPER_CACHE["surface"]
    surf = None
    try:
        pb = GdkPixbuf.Pixbuf.new_from_file(os.path.expanduser(path))
        pw, ph = pb.get_width(), pb.get_height()
        if pw > 0 and ph > 0:
            f = max(W / pw, H / ph)
            tw, th = max(1, int(pw * f + 0.5)), max(1, int(ph * f + 0.5))
            pb = pb.scale_simple(tw, th, GdkPixbuf.InterpType.BILINEAR)
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(W), int(H))
            cr = cairo.Context(surf)
            Gdk.cairo_set_source_pixbuf(cr, pb, (W - tw) / 2, (H - th) / 2)
            cr.paint()
            # Spines and shelf edges have to stay legible over whatever photo
            # happens to be on the desktop, so the wall is always knocked back.
            cr.set_source_rgba(0, 0, 0, max(0.0, min(1.0, float(dim))))
            cr.rectangle(0, 0, W, H)
            cr.fill()
            surf.flush()
    except Exception:
        surf = None
    _WALLPAPER_CACHE["key"] = key
    _WALLPAPER_CACHE["surface"] = surf
    return surf


class Shelf:
    """Draws the shelves, and owns the caches that keep drawing them cheap.

    The straightforward version laid out two Pango paragraphs per spine and
    rescaled every cover pixbuf on every single frame, so hovering one book
    re-rendered the whole library sixty times a second -- which is exactly why
    it felt heavy. Here the static furniture, each book at rest and the
    vignette are rendered once into image surfaces, so a frame during a hover
    animation is a few blits plus one live-drawn book, repainted only over the
    rectangle that actually changed.
    """

    ANIM = 0.32          # approach factor per frame
    LIFT = 9.0           # how far a hovered spine rises, in scaled px

    def __init__(self, cfg, books):
        self.cfg = cfg
        self.books = books
        self.hits = []
        self.hover = -1
        self.hover_progress = {}
        self._pb = {}
        self._covers = {}
        self._book_cache = {}
        self._static = None
        self._items = []
        self._index = {}
        self._key = None
        self._pad = 24
        self._books_sig = self._signature(books)
        self.top_inset = desktop_top_inset()
        # Drag-to-rearrange. drag_path is the book riding the pointer,
        # drop_index where it would land, drop_mark the caret drawn for it.
        self.drag_path = None
        self.drag_xy = None
        self.drop_index = None
        self.drop_mark = None

    # ------------------------------------------------------------ caches

    def set_books(self, books):
        self.books = books
        self._books_sig = self._signature(books)
        self.invalidate()

    @staticmethod
    def _signature(books):
        """One value standing in for the whole book list.

        _state_key runs on every frame, and building a tuple of several
        hundred paths sixty times a second to notice that nothing had changed
        was pure overhead. The list only changes when it is handed over, so it
        is summarised once, there.
        """
        h = hashlib.md5()
        for b in books:
            h.update(b["path"].encode("utf-8", "replace"))
            h.update(b"\0")
        return (len(books), h.hexdigest())

    def invalidate(self):
        """Drop every cache. Cheap to call; the next draw rebuilds lazily."""
        self._static = None
        self._book_cache.clear()
        self._covers.clear()
        self._items = []
        self._index = {}
        self._key = None
        self.top_inset = desktop_top_inset()

    def pixbuf(self, path):
        if path not in self._pb:
            try:
                self._pb[path] = GdkPixbuf.Pixbuf.new_from_file(path)
            except Exception:
                self._pb[path] = None
        return self._pb[path]

    def cover(self, path, w, h):
        """A cover scaled to the size it is actually drawn at, once."""
        if not path:
            return None
        key = (path, int(w), int(h))
        if key not in self._covers:
            pb = self.pixbuf(path)
            try:
                self._covers[key] = pb.scale_simple(
                    max(1, int(w)), max(1, int(h)),
                    GdkPixbuf.InterpType.BILINEAR) if pb else None
            except Exception:
                self._covers[key] = None
        return self._covers[key]

    def _state_key(self, W, H):
        c = self.cfg
        states = c.get("book_states") or {}
        return (int(W), int(H), c.get("display", "mixed"), float(c.get("scale", 1)),
                int(c.get("face_out_every", 0) or 0), self.top_inset,
                tuple(c.get("wall", ())), tuple(c.get("wood", ())),
                bool(c.get("tilt_books", True)), float(c.get("tilt_angle", 9.0)),
                c.get("wall_mode", "color"), c.get("wallpaper_path", ""),
                float(c.get("wallpaper_dim", 0.45)),
                self._books_sig,
                tuple(sorted(states.items())))

    def _ensure(self, W, H):
        key = self._state_key(W, H)
        if key == self._key and self._static is not None:
            return
        self._key = key
        self._book_cache.clear()
        self._pad = int(28 * float(self.cfg.get("scale", 1))) + 10
        self._build_shelf(W, H)
        self._index = {it["b"]["path"]: it for it in self._items}
        self.hits = list(self._items)

    # ------------------------------------------------------------ layout

    def book_state(self, b):
        """The per-book override: auto, cover, spine or tilt."""
        states = self.cfg.get("book_states") or {}
        return states.get(b["path"], "auto")

    def _kind_for(self, b, idx):
        """How this book stands: cover-forward, spine, or leaning spine.

        A per-book choice wins over the shelf-wide display mode, so one book
        can be turned face out on a shelf of spines without disturbing the
        rest.
        """
        state = self.book_state(b)
        if state == "cover":
            return "face" if b.get("cover") else "spine"
        if state == "spine":
            return "spine"
        if state == "tilt":
            return "tilt"
        mode = self.cfg.get("display", "mixed")
        if mode == "spine" or not b.get("cover"):
            return "spine"
        if mode == "cover":
            return "face"
        every = int(self.cfg.get("face_out_every", 0) or 0)
        return "face" if (every and idx % every == 0) else "spine"

    def _tilt_deg(self):
        if not self.cfg.get("tilt_books", True):
            return 0.0
        return max(0.0, min(30.0, float(self.cfg.get("tilt_angle", 9.0))))

    def _build_shelf(self, W, H):
        cfg = self.cfg
        s = float(cfg["scale"])
        wall, wood = cfg["wall"], cfg["wood"]
        self._items = []

        # Flush to the left, right and bottom edges of the display; only the
        # top is inset, to clear the shell bar.
        top = float(self.top_inset)
        upright = 22 * s
        x0, x1 = upright, W - upright
        avail = x1 - x0
        body_h = max(1.0, H - top)

        plank = 15 * s
        nominal = 190 * s + plank + 12 * s

        static = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(W), int(H))
        cr = cairo.Context(static)

        # The wall is either a flat gradient or the desktop wallpaper. With a
        # wallpaper behind them the shelves are drawn translucent, so the
        # picture reads through the case instead of being boxed out by it.
        paper = None
        if cfg.get("wall_mode") == "wallpaper" and cfg.get("wallpaper_path"):
            paper = wallpaper_surface(cfg["wallpaper_path"], W, H,
                                      cfg.get("wallpaper_dim", 0.45))
        if paper is not None:
            cr.set_source_surface(paper, 0, 0)
            cr.paint()
        else:
            g = cairo.LinearGradient(0, 0, 0, H)
            g.add_color_stop_rgb(0, *shade(wall, 1.5))
            g.add_color_stop_rgb(1, *shade(wall, 0.7))
            cr.set_source(g)
            cr.rectangle(0, 0, W, H)
            cr.fill()
        back = 0.40 if paper is not None else 1.0

        if not self.books:
            self._static = static
            self._empty_message(cr, 40 * s)
            return

        cr.set_source_rgba(*shade(wood, 0.35), back)
        cr.rectangle(0, top, W, body_h)
        cr.fill()

        # The cabinet is closed at the top by a beam, so the case reads as a
        # piece of furniture rather than as shelves floating on a wall.
        beam = max(10.0, plank * 1.15)
        wood_plank(cr, 0, top, W, beam, wood, 4242)
        cr.set_source_rgba(0, 0, 0, 0.38)
        cr.rectangle(0, top + beam, W, 16 * s)
        gsh = cairo.LinearGradient(0, top + beam, 0, top + beam + 16 * s)
        gsh.add_color_stop_rgba(0, 0, 0, 0, 0.5)
        gsh.add_color_stop_rgba(1, 0, 0, 0, 0.0)
        cr.set_source(gsh)
        cr.fill()
        top += beam
        body_h = max(1.0, H - top)
        rows = max(1, int(round(body_h / nominal)))
        row_h = body_h / rows
        maxh = max(40 * s, min(300 * s, row_h - plank - 12 * s))

        # Measure every book first, then spread them over the shelves the
        # cabinet actually has. Filling strictly left to right parks the whole
        # library on the top shelf and leaves the rest of the screen an empty
        # box, which is what it used to do.
        tilt_max = self._tilt_deg()
        n = len(self.books)
        sizes = []
        for idx, b in enumerate(self.books):
            rnd = random.Random(stable_seed(b["path"]))
            th = 22 * s + min(24 * s, (b.get("size", 0) / 1_400_000) * 14 * s)
            th += rnd.uniform(-2, 3) * s
            bh = maxh * rnd.uniform(0.74, 1.0)
            kind = self._kind_for(b, idx)
            w = bh * 0.66 if kind == "face" else th
            # A book asked to lean is given the width it sweeps as it tips, so
            # it tilts into room of its own rather than through its neighbour.
            deg = tilt_max if kind == "tilt" else 0.0
            sizes.append({"w": w, "h": bh, "kind": kind, "deg": deg,
                          "advance": w + bh * math.sin(math.radians(deg))})

        spacing = 1.2 * s
        span = avail - 8 * s
        groups = [[] for _ in range(rows)]
        used = [0.0] * rows
        per_row = max(1, math.ceil(n / rows))
        idx = 0
        for r in range(rows):
            while (idx < n and len(groups[r]) < per_row
                   and used[r] + sizes[idx]["advance"] <= span):
                groups[r].append(idx)
                used[r] += sizes[idx]["advance"] + spacing
                idx += 1
        r = 0
        while idx < n and r < rows:
            if used[r] + sizes[idx]["advance"] <= span:
                groups[r].append(idx)
                used[r] += sizes[idx]["advance"] + spacing
                idx += 1
            else:
                r += 1

        for r in range(rows):
            row_top = top + r * row_h
            y_base = row_top + row_h - plank

            gg = cairo.LinearGradient(0, row_top, 0, y_base)
            gg.add_color_stop_rgba(0, *shade(wall, 0.42), back)
            gg.add_color_stop_rgba(1, *shade(wall, 0.85), back)
            cr.set_source(gg)
            cr.rectangle(x0 - 4, row_top, avail + 8, y_base - row_top)
            cr.fill()

            x = x0 + 4 * s
            row_items = []
            for bi in groups[r]:
                sz = sizes[bi]
                row_items.append((bi, self.books[bi], x, sz))
                x += sz["advance"] + spacing

            if not row_items:
                wood_plank(cr, 0, y_base, W, plank, wood, r * 97)
                continue

            sg = cairo.LinearGradient(0, y_base - 26 * s, 0, y_base)
            sg.add_color_stop_rgba(0, 0, 0, 0, 0.0)
            sg.add_color_stop_rgba(1, 0, 0, 0, 0.55)
            cr.set_source(sg)
            cr.rectangle(x0, y_base - 26 * s, avail, 26 * s)
            cr.fill()

            gap = x1 - x
            last = len(row_items) - 1
            for k, (bi, b, bx, sz) in enumerate(row_items):
                deg = sz["deg"]
                if (deg == 0.0 and k == last and tilt_max > 0.0
                        and sz["kind"] == "spine" and gap > 14 * s):
                    # Tips into the empty end of the shelf, pivoting on the
                    # bottom corner that stays on the plank, and never further
                    # than the gap can take. Leaning the other way -- which is
                    # what this used to do -- swings the board straight through
                    # the book standing on its left.
                    room = min(gap - 8 * s, sz["h"])
                    deg = min(tilt_max, math.degrees(
                        math.asin(max(0.0, min(1.0, room / sz["h"])))))
                self._items.append({
                    "b": b, "x": bx, "y": y_base - sz["h"],
                    "w": sz["w"], "h": sz["h"],
                    "kind": "face" if sz["kind"] == "face" else "spine",
                    "deg": deg, "y_base": y_base, "s": s,
                    "row": r, "index": bi,
                })

            wood_plank(cr, 0, y_base, W, plank, wood, r * 97)

        for ux in (0, W - upright):
            g2 = cairo.LinearGradient(ux, 0, ux + upright, 0)
            g2.add_color_stop_rgb(0, *shade(wood, 1.15))
            g2.add_color_stop_rgb(1, *shade(wood, 0.5))
            cr.set_source(g2)
            cr.rectangle(ux, top, upright, body_h)
            cr.fill()

        self._paint_vignette(cr, W, H, 0.30 if paper is not None else 0.45)
        static.flush()
        self._static = static

    def _paint_vignette(self, cr, W, H, depth):
        vg = cairo.RadialGradient(W / 2, H / 2, min(W, H) * 0.25,
                                  W / 2, H / 2, max(W, H) * 0.75)
        vg.add_color_stop_rgba(0, 0, 0, 0, 0)
        vg.add_color_stop_rgba(1, 0, 0, 0, depth)
        cr.set_source(vg)
        cr.rectangle(0, 0, W, H)
        cr.fill()

    def _empty_message(self, cr, margin):
        cr.set_source_rgb(0.6, 0.55, 0.5)
        l = PangoCairo.create_layout(cr)
        l.set_font_description(Pango.FontDescription("Inter 16"))
        l.set_text(f"no epubs in {self.cfg['books_dir']}", -1)
        cr.move_to(margin, margin + self.top_inset)
        PangoCairo.show_layout(cr, l)

    # ------------------------------------------------------------ books

    def _render_book(self, cr, item, prog):
        b, s = item["b"], item["s"]
        x, y, w, h = item["x"], item["y"], item["w"], item["h"]
        if item["kind"] == "face":
            pb = self.cover(b.get("cover"), w, h)
            if pb:
                draw_faceout(cr, b, pb, x, y + h, w, h, prog=prog)
                return
        draw_spine(cr, b, x, y + h, w, h, s, prog=prog)

    def _book_surface(self, item):
        key = (item["b"]["path"], item["kind"],
               round(item["w"], 1), round(item["h"], 1))
        surf = self._book_cache.get(key)
        if surf is None:
            pad = self._pad
            sw = int(math.ceil(item["w"])) + pad * 2
            sh = int(math.ceil(item["h"])) + pad * 2
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, sw, sh)
            local = dict(item, x=pad, y=pad)
            self._render_book(cairo.Context(surf), local, 0.0)
            surf.flush()
            self._book_cache[key] = surf
        return surf

    # ------------------------------------------------------------ animation

    def is_animating(self):
        if self.hover_progress:
            return True
        return self.hover != -1 and self.hover in self._index

    def rect_for(self, path):
        it = self._index.get(path)
        if not it:
            return None
        pad = self._pad
        # A leaning book reaches past its upright box; padding the rectangle by
        # the sweep keeps the hover repaint from leaving a smear behind it.
        sweep = it["h"] * math.sin(math.radians(it.get("deg", 0.0)))
        return (it["x"] - pad, it["y"] - pad,
                it["w"] + pad * 2 + sweep, it["h"] + pad * 2)

    def animate_step(self):
        """Advance the hover animation; returns the rectangle to repaint."""
        keys = set(self.hover_progress)
        if self.hover != -1:
            keys.add(self.hover)
        if not keys:
            return None
        touched = []
        for k in keys:
            cur = self.hover_progress.get(k, 0.0)
            target = 1.0 if k == self.hover else 0.0
            new = target if abs(cur - target) < 0.004 \
                else cur + (target - cur) * self.ANIM
            if new <= 0.002 and target == 0.0:
                if self.hover_progress.pop(k, None) is not None:
                    touched.append(k)
                continue
            if self.hover_progress.get(k) != new:
                self.hover_progress[k] = new
                touched.append(k)
        rects = [r for r in (self.rect_for(k) for k in touched) if r]
        if not rects:
            return None
        x0 = min(r[0] for r in rects)
        y0 = min(r[1] for r in rects)
        x1 = max(r[0] + r[2] for r in rects)
        y1 = max(r[1] + r[3] for r in rects)
        return (x0, y0, x1 - x0, y1 - y0)

    # ------------------------------------------------------------ dragging

    def _insertion(self, px, py):
        """Where a book dropped at (px, py) lands: (index, caret rectangle)."""
        if not self._items:
            return 0, None
        rows = {}
        for it in self._items:
            rows.setdefault(it["row"], []).append(it)
        # Nearest shelf first: dropping below the last plank should mean the
        # end of that shelf, not the nearest book measured diagonally.
        best_row, best_d = None, None
        for r, items in rows.items():
            hi = min(i["y"] for i in items)
            lo = items[0]["y_base"]
            d = 0.0 if hi <= py <= lo else min(abs(py - hi), abs(py - lo))
            if best_d is None or d < best_d:
                best_row, best_d = r, d
        items = sorted(rows[best_row], key=lambda i: i["x"])
        top = min(i["y"] for i in items)
        base = items[0]["y_base"]
        for it in items:
            if px < it["x"] + it["w"] / 2:
                return it["index"], (it["x"] - 2, top, base)
        tail = items[-1]
        return tail["index"] + 1, (tail["x"] + tail["w"] + 1, top, base)

    def set_drop(self, px, py):
        """Aim the drop caret. True when it moved and a repaint is due."""
        idx, mark = self._insertion(px, py)
        changed = idx != self.drop_index or mark != self.drop_mark
        self.drop_index = idx
        self.drop_mark = mark
        return changed

    def clear_drag(self):
        self.drag_path = None
        self.drag_xy = None
        self.drop_index = None
        self.drop_mark = None

    # ------------------------------------------------------------ drawing

    def draw(self, area, cr, W, H):
        self._ensure(W, H)
        if self._static is not None:
            # The cached furniture is fully opaque, so it can be copied rather
            # than blended.
            cr.save()
            cr.set_operator(cairo.OPERATOR_SOURCE)
            cr.set_source_surface(self._static, 0, 0)
            cr.paint()
            cr.restore()
        if not self._items:
            return

        pad = self._pad
        for it in self._items:
            if it["b"]["path"] == self.drag_path:
                # It is riding the pointer instead; leaving a copy on the shelf
                # makes the drag look like a duplication rather than a move.
                continue
            prog = self.hover_progress.get(it["b"]["path"], 0.0)
            deg = it.get("deg", 0.0)
            cr.save()
            if deg:
                # Pivots on the bottom corner that stays on the plank.
                px, py = it["x"] + it["w"], it["y_base"]
                cr.translate(px, py)
                cr.rotate(math.radians(deg))
                cr.translate(-px, -py)
            if prog > 0.005:
                self._render_book(cr, it, prog)
            else:
                surf = self._book_surface(it)
                sx, sy = it["x"] - pad, it["y"] - pad
                cr.set_source_surface(surf, sx, sy)
                cr.rectangle(sx, sy, surf.get_width(), surf.get_height())
                cr.fill()
            cr.restore()

        if self.drop_mark:
            mx, my0, my1 = self.drop_mark
            cr.set_source_rgba(1.0, 0.85, 0.45, 0.9)
            cr.rectangle(mx - 1.5, my0, 3, my1 - my0)
            cr.fill()

        if self.drag_path and self.drag_xy:
            it = self._index.get(self.drag_path)
            if it:
                surf = self._book_surface(it)
                dx, dy = self.drag_xy
                cr.save()
                cr.set_source_surface(surf, dx - surf.get_width() / 2,
                                      dy - surf.get_height() / 2)
                cr.paint_with_alpha(0.85)
                cr.restore()

    def at(self, px, py):
        """The book under the pointer, tilt included."""
        for it in reversed(self.hits):
            x, y, w, h = it["x"], it["y"], it["w"], it["h"]
            deg = it.get("deg", 0.0)
            qx, qy = px, py
            if deg:
                # Undo the lean, then test the book's own upright box.
                a = math.radians(deg)
                cx, cy = x + w, it["y_base"]
                dx, dy = px - cx, py - cy
                qx = cx + dx * math.cos(a) + dy * math.sin(a)
                qy = cy - dx * math.sin(a) + dy * math.cos(a)
            if x <= qx <= x + w and y <= qy <= y + h:
                return it["b"]
        return None


# ---------------------------------------------------------------- chapters

class _BlockExtractor(HTMLParser):
    """Split one spine document into typed blocks: ("h", text) | ("p", text).

    The old extractor flattened everything to a wall of lines, which is why
    the reader could not tell a chapter heading from a paragraph. Keeping the
    distinction is what lets the reader lay text out like a book.
    """

    HEAD = {"h1", "h2", "h3", "h4", "h5", "h6"}
    BLOCK = {"p", "div", "li", "tr", "blockquote", "dd", "dt", "pre",
             "figcaption", "td", "section", "article"}
    SKIP = {"script", "style", "head", "title"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self.buf = []
        self.kind = "p"
        self.skip = 0
        self.head_depth = 0

    def _flush(self):
        text = " ".join("".join(self.buf).split())
        self.buf = []
        if text:
            self.blocks.append((self.kind, text))
        self.kind = "h" if self.head_depth else "p"

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
            return
        if tag == "br":
            self.buf.append("\n")
            return
        if tag in self.HEAD:
            self._flush()
            self.head_depth += 1
            self.kind = "h"
        elif tag in self.BLOCK:
            self._flush()

    def handle_startendtag(self, tag, attrs):
        if tag == "br":
            self.buf.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if tag in self.HEAD:
            self._flush()
            self.head_depth = max(0, self.head_depth - 1)
            self.kind = "h" if self.head_depth else "p"
        elif tag in self.BLOCK:
            self._flush()

    def handle_data(self, data):
        if not self.skip:
            self.buf.append(data)

    def result(self):
        self._flush()
        return self.blocks


def _toc_labels(z, opf_path):
    """href (no fragment) -> label, from the EPUB3 nav doc or the NCX."""
    labels = {}
    base = _opf_dir(opf_path)
    try:
        opf = ET.fromstring(z.read(opf_path))
    except Exception:
        return labels

    items = {}
    nav_href = ncx_href = None
    for it in opf.iter(f"{OPF}item"):
        iid, href = it.get("id"), it.get("href")
        if iid and href:
            items[iid] = href
        if "nav" in (it.get("properties") or "").split():
            nav_href = href
        if (it.get("media-type") or "") == "application/x-dtbncx+xml":
            ncx_href = href
    if ncx_href is None:
        spine = opf.find(f"{OPF}spine")
        if spine is not None and spine.get("toc"):
            ncx_href = items.get(spine.get("toc"))

    def norm(href, doc_base):
        href = href.split("#")[0]
        if not href:
            return None
        return os.path.normpath(doc_base + href).replace("\\", "/")

    if nav_href:
        full = norm(nav_href, base)
        try:
            raw = z.read(full).decode("utf-8", errors="replace")
        except Exception:
            raw = None
        if raw:
            doc_base = _opf_dir(full)

            class _Nav(HTMLParser):
                def __init__(self):
                    super().__init__(convert_charrefs=True)
                    self.href = None
                    self.buf = []

                def handle_starttag(self, tag, attrs):
                    if tag == "a":
                        self.href = dict(attrs).get("href")
                        self.buf = []

                def handle_endtag(self, tag):
                    if tag == "a" and self.href:
                        text = " ".join("".join(self.buf).split())
                        key = norm(self.href, doc_base)
                        if key and text:
                            labels.setdefault(key, text)
                        self.href = None

                def handle_data(self, data):
                    if self.href is not None:
                        self.buf.append(data)

            try:
                _Nav().feed(raw)
            except Exception:
                pass

    if not labels and ncx_href:
        full = norm(ncx_href, base)
        try:
            ncx = ET.fromstring(z.read(full))
        except Exception:
            ncx = None
        if ncx is not None:
            doc_base = _opf_dir(full)
            NCX = "{http://www.daisy.org/z3986/2005/ncx/}"
            for nav in ncx.iter(f"{NCX}navPoint"):
                lbl = nav.find(f"{NCX}navLabel/{NCX}text")
                content = nav.find(f"{NCX}content")
                if lbl is None or content is None:
                    continue
                key = norm(content.get("src") or "", doc_base)
                text = (lbl.text or "").strip()
                if key and text:
                    labels.setdefault(key, text)
    return labels


# Some epubs put a third of the book in one spine document. Reading those is
# no better than the old flat dump, so oversized documents are re-split on
# their own headings.
SPLIT_OVER_CHARS = 40_000
SPLIT_MIN_PART = 1_200


def _split_on_headings(blocks, fallback_title):
    total = sum(len(t) for _, t in blocks)
    heads = [i for i, (kind, _) in enumerate(blocks) if kind == "h"]
    if total <= SPLIT_OVER_CHARS or len(heads) < 3:
        return None

    parts, start = [], 0
    for i in heads:
        if i > start:
            parts.append(blocks[start:i])
            start = i
    parts.append(blocks[start:])

    # Merge runs of front matter or stacked headings into the section they
    # introduce, so the contents list is chapters rather than stray lines.
    merged = []
    for part in parts:
        size = sum(len(t) for _, t in part)
        if merged and size < SPLIT_MIN_PART:
            merged[-1].extend(part)
        else:
            merged.append(list(part))
    if len(merged) < 2:
        return None

    out = []
    for part in merged:
        title = next((t for kind, t in part if kind == "h"), None) or fallback_title
        out.append({
            "title": title[:120],
            "blocks": part,
            "chars": sum(len(t) for _, t in part),
        })
    return out


def extract_epub_chapters(path):
    """[{title, blocks, chars}] in spine order. Empty documents are dropped."""
    chapters = []
    with zipfile.ZipFile(path) as z:
        opf_path = _opf_path(z)
        hrefs = _spine_hrefs(z, opf_path)
        if not hrefs:
            hrefs = sorted(n for n in z.namelist()
                           if n.lower().endswith((".xhtml", ".html", ".htm")))
        labels = _toc_labels(z, opf_path)
        for href in hrefs:
            try:
                raw = z.read(href).decode("utf-8", errors="replace")
            except Exception:
                continue
            ex = _BlockExtractor()
            try:
                ex.feed(raw)
            except Exception:
                continue
            blocks = ex.result()
            if not blocks:
                continue
            title = labels.get(href)
            if not title:
                for kind, text in blocks[:4]:
                    if kind == "h":
                        title = text
                        break
            if not title:
                title = Path(href).stem.replace("_", " ").replace("-", " ").title()
            split = _split_on_headings(blocks, title)
            if split:
                chapters.extend(split)
            else:
                chapters.append({
                    "title": title[:120],
                    "blocks": blocks,
                    "chars": sum(len(t) for _, t in blocks),
                })
    return chapters


# ---------------------------------------------------------------- progress

PROGRESS = CACHE / "progress.json"


def load_progress():
    try:
        return json.loads(PROGRESS.read_text())
    except Exception:
        return {}


def save_progress(store):
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        PROGRESS.write_text(json.dumps(store, indent=1))
    except Exception as e:
        print("progress save failed:", e, file=sys.stderr)


# ---------------------------------------------------------------- theming

SHELL_COLORS = (Path(GLib.get_user_state_dir()) / "quickshell" / "user"
                / "generated" / "colors.json")


def _hex_rgb(s):
    s = s.lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        raise ValueError(s)
    return [int(s[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]


def shell_palette():
    """Material colors generated by illogical-impulse, or None."""
    try:
        raw = json.loads(SHELL_COLORS.read_text())
    except Exception:
        return None
    try:
        return {k: _hex_rgb(v) for k, v in raw.items()
                if isinstance(v, str) and v.startswith("#")}
    except Exception:
        return None


def themed_config(cfg):
    """Overlay shell colors onto the config when theme_from_shell is on."""
    if not cfg.get("theme_from_shell", True):
        return cfg
    pal = shell_palette()
    if not pal:
        return cfg
    cfg = dict(cfg)
    if "surface_container_lowest" in pal:
        cfg["wall"] = pal["surface_container_lowest"]
    elif "background" in pal:
        cfg["wall"] = pal["background"]
    # Keep the wood woody: tint the configured plank color toward the accent
    # instead of replacing it, or shelves turn into flat colored bars.
    accent = pal.get("primary_container") or pal.get("primary")
    if accent:
        base = cfg.get("wood", DEFAULTS["wood"])
        cfg["wood"] = [b * 0.72 + a * 0.28 for b, a in zip(base, accent)]
    for key, src in (("reader_bg", "surface_container"),
                     ("reader_fg", "on_surface"),
                     ("reader_dim", "on_surface_variant"),
                     ("reader_accent", "primary")):
        if src in pal:
            cfg[key] = pal[src]
    return cfg


def rgb_css(c, alpha=None):
    r, g, b = (max(0, min(255, int(round(v * 255)))) for v in c[:3])
    if alpha is None:
        return f"#{r:02x}{g:02x}{b:02x}"
    return f"rgba({r},{g},{b},{alpha})"


# ---------------------------------------------------------------- ipc

def ipc_socket_path():
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/shelfwall-{os.getuid()}"
    sig = os.environ.get("WAYLAND_DISPLAY", "wayland-0")
    return Path(base) / f"shelfwall-{sig}.sock"


def ipc_send(command, timeout=1.0):
    """Send a command to a running instance. True if it was delivered."""
    import socket as _socket
    path = str(ipc_socket_path())
    try:
        s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(path)
        s.sendall((command.strip() + "\n").encode())
        # Read to end of line or end of stream. A fixed 256-byte read
        # silently truncated the replies that are worth having -- `get`
        # returns the whole config, which outgrew it long ago.
        chunks = []
        try:
            while True:
                part = s.recv(4096)
                if not part:
                    break
                chunks.append(part)
                if part.endswith(b"\n"):
                    break
        except Exception:
            pass
        reply = b"".join(chunks).decode(errors="replace").strip()
        s.close()
        if reply:
            print(reply)
        return True
    except Exception:
        return False


class IPCServer:
    """Tiny line-based control socket so quickshell can drive the shelf."""

    def __init__(self, handler):
        self.handler = handler
        self.path = ipc_socket_path()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                self.path.unlink()
        except Exception:
            pass
        self.service = Gio.SocketService.new()
        try:
            addr = Gio.UnixSocketAddress.new(str(self.path))
            self.service.add_address(addr, Gio.SocketType.STREAM,
                                     Gio.SocketProtocol.DEFAULT, None)
        except Exception as e:
            print("ipc unavailable:", e, file=sys.stderr)
            return
        self.service.connect("incoming", self._on_conn)
        self.service.start()

    def _on_conn(self, service, conn, source):
        try:
            istream = Gio.DataInputStream.new(conn.get_input_stream())
            line, _ = istream.read_line_utf8(None)
            if line is None:
                return True
            reply = self.handler(line.strip()) or "ok"
            conn.get_output_stream().write((str(reply) + "\n").encode(), None)
            conn.close(None)
        except Exception as e:
            print("ipc error:", e, file=sys.stderr)
        return True

    def shutdown(self):
        try:
            self.service.stop()
            if self.path.exists():
                self.path.unlink()
        except Exception:
            pass


# ---------------------------------------------------------------- epub reader

READER_DEFAULTS = {
    # Full bleed by default: the measure cap is still one switch away, but a
    # reader that arrives already using the whole screen is what people expect.
    "reader_full_width": True,
    "reader_width": 46,        # measure, in "em" of the reading font
    "reader_font": "Literata, Bookerly, Georgia, serif",
    "reader_font_size": 13,    # pt
    "reader_line_height": 1.55,
    "reader_justify": True,
    "reader_bg": [0.09, 0.085, 0.08],
    "reader_fg": [0.88, 0.86, 0.82],
    "reader_dim": [0.60, 0.58, 0.55],
    "reader_accent": [0.85, 0.62, 0.35],
}

DEFAULTS.update(READER_DEFAULTS)


class EpubReaderView(Gtk.Box):
    """A reading view, not a text dump.

    Text is laid out one chapter at a time inside a capped measure, the way
    calibre does it: a full-width line of prose on a 1080p screen is close to
    200 characters, which is unreadable, so the column is limited and centred
    and the surplus width becomes margin.
    """

    def __init__(self, cfg, on_close=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.cfg = cfg
        self.on_close = on_close
        self.book = None
        self.chapters = []
        self.chapter = 0
        self.chapter_starts = []
        self.total_chars = 1
        self.matches = []
        self.match_idx = -1
        self.progress = load_progress()
        self._restore_frac = None
        self.font_size = float(cfg.get("reader_font_size",
                                       READER_DEFAULTS["reader_font_size"]))

        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self.css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.add_css_class("sw-reader")
        self._build_header()
        self._build_body()
        self._build_footer()
        self._apply_css()

    # ------------------------------------------------------------ chrome

    def _build_header(self):
        """A find bar, and nothing else.

        Every control that used to sit across the top of the page -- contents,
        text size, measure, font -- now lives in the settings card, so the
        reader is the page and only the page. The find bar is built hidden and
        appears for the length of a search.
        """
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.add_css_class("sw-bar")
        bar.set_margin_top(8)
        bar.set_margin_bottom(4)
        bar.set_margin_start(14)
        bar.set_margin_end(14)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Find in book")
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("search-changed", self.on_search_changed)
        self.search_entry.connect("activate", lambda e: self.next_match())
        # Escape inside the entry should return to the page, not close the book.
        esc = Gtk.EventControllerKey()
        esc.connect("key-pressed", self._on_search_key)
        self.search_entry.add_controller(esc)
        bar.append(self.search_entry)

        self.match_label = Gtk.Label(label="")
        self.match_label.add_css_class("sw-dim")
        bar.append(self.match_label)

        self.find_bar = bar
        self.find_bar.set_visible(False)
        self.append(bar)


    def _build_body(self):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        row.set_vexpand(True)

        self.toc_list = Gtk.ListBox()
        self.toc_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.toc_list.connect("row-activated", self._on_toc_row)
        self.toc_list.connect("row-selected", self._on_toc_row)
        toc_scroll = Gtk.ScrolledWindow()
        toc_scroll.set_child(self.toc_list)
        toc_scroll.set_vexpand(True)
        self.toc_pane = toc_scroll
        self.toc_pane.add_css_class("sw-toc")
        self.toc_pane.set_size_request(280, -1)
        self.toc_pane.set_visible(False)
        row.append(self.toc_pane)

        self.buffer = Gtk.TextBuffer()
        self.tag_body = self.buffer.create_tag("body")
        self.tag_head = self.buffer.create_tag(
            "head", weight=Pango.Weight.BOLD, scale=1.45,
            pixels_above_lines=26, pixels_below_lines=14,
            justification=Gtk.Justification.LEFT)
        self.tag_first = self.buffer.create_tag("first", indent=0)
        self.tag_match = self.buffer.create_tag(
            "match", background="#ffd54f", foreground="#1a1a1a")
        self.tag_current = self.buffer.create_tag(
            "current", background="#ff9800", foreground="#1a1a1a")

        self.textview = Gtk.TextView(buffer=self.buffer)
        self.textview.set_editable(False)
        self.textview.set_cursor_visible(False)
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.textview.set_top_margin(28)
        self.textview.set_bottom_margin(80)
        self.textview.set_monospace(False)

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_child(self.textview)
        self.scroller.set_hexpand(True)
        self.scroller.set_vexpand(True)
        self.scroller.connect("notify::width", lambda *a: self._relayout())
        self.scroller.get_vadjustment().connect(
            "value-changed", lambda *a: self._on_scrolled())
        row.append(self.scroller)

        self.append(row)

    def _build_footer(self):
        """The running foot: what you are reading, and how far in you are."""
        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        foot.add_css_class("sw-bar")
        foot.set_margin_start(14)
        foot.set_margin_end(14)
        foot.set_margin_top(4)
        foot.set_margin_bottom(8)

        self.title_label = Gtk.Label(label="", xalign=0)
        self.title_label.add_css_class("sw-dim")
        self.title_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.title_label.set_hexpand(True)
        foot.append(self.title_label)

        self.chapter_label = Gtk.Label(label="", xalign=1)
        self.chapter_label.add_css_class("sw-dim")
        self.chapter_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.chapter_label.set_max_width_chars(42)
        foot.append(self.chapter_label)

        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_size_request(150, -1)
        self.progress_bar.set_valign(Gtk.Align.CENTER)
        foot.append(self.progress_bar)

        self.progress_label = Gtk.Label(label="")
        self.progress_label.add_css_class("sw-dim")
        self.progress_label.set_width_chars(4)
        foot.append(self.progress_label)

        self.append(foot)


    # ------------------------------------------------------------ styling

    def _apply_css(self):
        c = self.cfg
        bg = rgb_css(c.get("reader_bg", READER_DEFAULTS["reader_bg"]))
        fg = rgb_css(c.get("reader_fg", READER_DEFAULTS["reader_fg"]))
        dim = rgb_css(c.get("reader_dim", READER_DEFAULTS["reader_dim"]))
        accent = rgb_css(c.get("reader_accent", READER_DEFAULTS["reader_accent"]))
        family = c.get("reader_font", READER_DEFAULTS["reader_font"])
        css = f"""
        .sw-reader {{ background: {bg}; color: {fg}; }}
        .sw-reader textview {{
            font-family: {family};
            font-size: {self.font_size}pt;
            background: {bg};
            color: {fg};
        }}
        .sw-reader textview text {{ background: {bg}; color: {fg}; }}
        .sw-title {{ font-weight: 700; font-size: 12pt; }}
        .sw-dim {{ color: {dim}; font-size: 9pt; }}
        .sw-bar {{ background: {bg}; }}
        .sw-toc {{ background: {bg}; border-right: 1px solid {dim}; }}
        .sw-toc row {{ padding: 7px 14px; }}
        .sw-toc row:selected {{ background: {accent}; color: {bg}; }}
        .sw-reader progressbar progress {{ background: {accent}; }}
        .sw-card {{
            background: {bg};
            color: {fg};
            border: 1px solid {dim};
            border-radius: 18px;
        }}
        .sw-card-title {{ font-weight: 700; font-size: 14pt; }}
        .sw-card-section {{
            font-weight: 700;
            font-size: 10pt;
            color: {accent};
            text-transform: uppercase;
        }}
        .sw-card-hint {{ color: {dim}; font-size: 9pt; }}
        /* Catches every click that misses a card, and says visually that the
           card is the only thing listening. */
        .sw-scrim {{ background: rgba(0, 0, 0, 0.38); }}
        """
        self.css.load_from_data(css.encode())

    def _relayout(self):
        """Cap the text column and turn the surplus width into margin."""
        width = self.scroller.get_width()
        if width <= 0:
            return
        ctx = self.textview.get_pango_context()
        metrics = ctx.get_metrics(ctx.get_font_description(), None)
        em = metrics.get_approximate_char_width() / Pango.SCALE
        if em <= 0:
            em = self.font_size * 1.1
        if self.cfg.get("reader_full_width", True):
            # Edge to edge, bar a hairline gutter so the first and last glyph
            # of a line are not flush against the bezel.
            margin = 16
        else:
            measure = em * float(self.cfg.get("reader_width",
                                              READER_DEFAULTS["reader_width"]))
            measure = min(measure, width - 48)
            margin = max(24, int((width - measure) / 2))
        self.textview.set_left_margin(margin)
        self.textview.set_right_margin(margin)

        line_height = float(self.cfg.get("reader_line_height",
                                         READER_DEFAULTS["reader_line_height"]))
        extra = int(self.font_size * 1.33 * (line_height - 1.0))
        self.tag_body.set_property("pixels_inside_wrap", max(0, extra))
        self.tag_body.set_property("pixels_below_lines", max(2, int(extra * 1.4)))
        self.tag_body.set_property("indent", int(em * 1.6))
        # Set both ways round: only ever switching justification *on* left the
        # text filled for the rest of the session once the switch was used.
        self.tag_body.set_property(
            "justification",
            Gtk.Justification.FILL if self.cfg.get("reader_justify", True)
            else Gtk.Justification.LEFT)

    def bump_font(self, step):
        self.font_size = max(7.0, min(28.0, self.font_size + step))
        self.cfg["reader_font_size"] = self.font_size
        save_config(self.cfg)
        self._apply_css()
        self._relayout()

    def font_reset(self):
        self.font_size = float(READER_DEFAULTS["reader_font_size"])
        self.cfg["reader_font_size"] = self.font_size
        save_config(self.cfg)
        self._apply_css()
        self._relayout()

    def apply_top_inset(self):
        """Keep the page clear of the shell bar.

        The reader covers the same layer surface as the shelf, and the bar
        floats above both of them, so without this the first line of a chapter
        reads out from underneath it.
        """
        self.set_margin_top(int(max(0.0, desktop_top_inset())))

    # ------------------------------------------------------------ opening

    def open(self, book):
        self.book = book
        label = book["title"]
        if book.get("author"):
            label = f'{book["title"]} — {book["author"]}'
        self.title_label.set_text(label)
        try:
            self.chapters = extract_epub_chapters(book["path"])
        except Exception as e:
            self.chapters = [{"title": "Error",
                              "blocks": [("p", f"Could not open this book: {e}")],
                              "chars": 1}]
        if not self.chapters:
            self.chapters = [{"title": "Empty",
                              "blocks": [("p", "No readable text in this file.")],
                              "chars": 1}]

        self.chapter_starts = []
        running = 0
        for ch in self.chapters:
            self.chapter_starts.append(running)
            running += max(1, ch["chars"])
        self.total_chars = max(1, running)

        self._fill_toc()
        saved = self.progress.get(book["path"], {})
        idx = min(int(saved.get("chapter", 0)), len(self.chapters) - 1)
        self._restore_frac = float(saved.get("frac", 0.0))
        self.show_chapter(max(0, idx), restore=True)

        self.search_entry.set_text("")
        self.matches = []
        self.match_idx = -1
        self.match_label.set_text("")
        self.find_bar.set_visible(False)
        self.toggle_toc(False)
        self.apply_top_inset()
        self.set_visible(True)
        self.grab_page_focus()

    def _fill_toc(self):
        child = self.toc_list.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.toc_list.remove(child)
            child = nxt
        for i, ch in enumerate(self.chapters):
            lbl = Gtk.Label(label=f'{i + 1}.  {ch["title"]}', xalign=0)
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            lbl.set_wrap(False)
            row = Gtk.ListBoxRow()
            row.set_child(lbl)
            row.chapter_index = i
            self.toc_list.append(row)

    def _on_toc_row(self, listbox, row):
        if row is None:
            return
        idx = getattr(row, "chapter_index", None)
        if idx is not None and idx != self.chapter:
            self.show_chapter(idx)

    def show_chapter(self, idx, restore=False, at_end=False):
        idx = max(0, min(idx, len(self.chapters) - 1))
        self.chapter = idx
        ch = self.chapters[idx]
        self.buffer.set_text("")
        it = self.buffer.get_end_iter()
        first_para = True
        for kind, text in ch["blocks"]:
            start_off = it.get_offset()
            self.buffer.insert(it, text + "\n")
            s = self.buffer.get_iter_at_offset(start_off)
            e = self.buffer.get_end_iter()
            if kind == "h":
                self.buffer.apply_tag(self.tag_head, s, e)
                first_para = True
            else:
                self.buffer.apply_tag(self.tag_body, s, e)
                if first_para:
                    self.buffer.apply_tag(self.tag_first, s, e)
                    first_para = False
            it = self.buffer.get_end_iter()

        self.chapter_label.set_text(
            f'{ch["title"]}   ·   {idx + 1} of {len(self.chapters)}')
        row = self.toc_list.get_row_at_index(idx)
        if row and self.toc_list.get_selected_row() is not row:
            self.toc_list.select_row(row)

        self._relayout()
        frac = self._restore_frac if restore else (1.0 if at_end else 0.0)
        self._restore_frac = None
        GLib.idle_add(self._scroll_to_fraction, frac or 0.0)
        self._update_progress()
        self._reapply_matches()

    def _scroll_to_fraction(self, frac):
        adj = self.scroller.get_vadjustment()
        span = max(0.0, adj.get_upper() - adj.get_page_size())
        adj.set_value(span * max(0.0, min(1.0, frac)))
        self._update_progress()
        return False

    # ------------------------------------------------------------ progress

    def _scroll_fraction(self):
        adj = self.scroller.get_vadjustment()
        span = adj.get_upper() - adj.get_page_size()
        if span <= 0:
            # Either the chapter fits on one screen or GTK has not measured it
            # yet. Reporting 1.0 here would file a just-opened book away as
            # finished, so an unscrollable chapter counts as unstarted.
            return 0.0
        return max(0.0, min(1.0, adj.get_value() / span))

    def _on_scrolled(self):
        self._update_progress()

    def overall_fraction(self):
        """How far through the whole book this position is, 0-1."""
        if not self.chapters:
            return 0.0
        frac = self._scroll_fraction()
        done = self.chapter_starts[self.chapter] + \
            max(1, self.chapters[self.chapter]["chars"]) * frac
        return max(0.0, min(1.0, done / self.total_chars))

    def _update_progress(self):
        if not self.chapters:
            return
        overall = self.overall_fraction()
        self.progress_bar.set_fraction(overall)
        self.progress_label.set_text(f"{overall * 100:.0f}%")

    def remember(self):
        if not self.book:
            return
        # The overall percentage is stored alongside the position because the
        # book information card wants it without paying to parse the epub.
        self.progress[self.book["path"]] = {
            "chapter": self.chapter,
            "chapters": len(self.chapters),
            "frac": round(self._scroll_fraction(), 4),
            "pct": round(self.overall_fraction() * 100, 1),
            "title": self.book.get("title", ""),
            "at": int(time.time()),
        }
        save_progress(self.progress)

    # ------------------------------------------------------------ movement

    def grab_page_focus(self):
        self.textview.grab_focus()

    def scroll_by(self, pixels):
        adj = self.scroller.get_vadjustment()
        top = max(0.0, adj.get_upper() - adj.get_page_size())
        adj.set_value(max(0.0, min(top, adj.get_value() + pixels)))

    def at_bottom(self):
        adj = self.scroller.get_vadjustment()
        return adj.get_value() >= adj.get_upper() - adj.get_page_size() - 2

    def at_top(self):
        return self.scroller.get_vadjustment().get_value() <= 2

    def _snap_to_line(self, want_top, forward):
        """The scroll offset nearest `want_top` that starts on a whole line.

        Turning the page by a fixed fraction of the viewport cuts whichever
        line straddles the fold in half, so every turn either repeated a line
        or skipped one. Asking the layout which line owns the boundary and
        aligning to that line's own top edge is what makes a page turn land on
        exactly the text the last screen ran out on.
        """
        tv = self.textview
        adj = self.scroller.get_vadjustment()
        limit = max(0.0, adj.get_upper() - adj.get_page_size())
        want_top = max(0.0, min(limit, want_top))
        try:
            _, by = tv.window_to_buffer_coords(
                Gtk.TextWindowType.WIDGET, 0,
                int(round(want_top - adj.get_value())))
            it, line_top = tv.get_line_at_y(by)
            line_h = tv.get_line_yrange(it)[1]
            _, wy = tv.buffer_to_window_coords(
                Gtk.TextWindowType.WIDGET, 0, line_top)
        except Exception:
            return want_top
        top = adj.get_value() + wy
        if not forward and top < want_top - 0.5:
            # Reading backwards, the page should still begin on a whole line,
            # so the boundary line goes to the bottom of the new screen rather
            # than showing its lower half at the top.
            top += line_h
        return max(0.0, min(limit, top))

    def page(self, direction):
        """Turn a page, rolling into the next/previous chapter at the edges."""
        adj = self.scroller.get_vadjustment()
        page = adj.get_page_size()
        if direction > 0 and self.at_bottom():
            if self.chapter < len(self.chapters) - 1:
                self.remember()
                self.show_chapter(self.chapter + 1)
            return
        if direction < 0 and self.at_top():
            if self.chapter > 0:
                self.remember()
                self.show_chapter(self.chapter - 1, at_end=True)
            return
        value = adj.get_value()
        target = self._snap_to_line(value + page * direction, direction > 0)
        # A block taller than the viewport has no line boundary to land on and
        # would pin the page in place, so the key falls back to a plain scroll.
        if (target - value) * direction < 2:
            target = value + page * 0.92 * direction
        limit = max(0.0, adj.get_upper() - page)
        adj.set_value(max(0.0, min(limit, target)))

    def next_chapter(self):
        if self.chapter < len(self.chapters) - 1:
            self.remember()
            self.show_chapter(self.chapter + 1)

    def prev_chapter(self):
        if self.chapter > 0:
            self.remember()
            self.show_chapter(self.chapter - 1)

    def toggle_toc(self, on=None):
        show = (not self.toc_pane.get_visible()) if on is None else bool(on)
        self.toc_pane.set_visible(show)
        if not show:
            self.grab_page_focus()

    def close(self):
        self.remember()
        self.find_bar.set_visible(False)
        self.set_visible(False)
        if self.on_close:
            self.on_close()

    # ------------------------------------------------------------ search

    def focus_search(self):
        self.find_bar.set_visible(True)
        self.search_entry.grab_focus()
        self.search_entry.select_region(0, -1)

    def hide_search(self):
        """Put the page back the way it was and get out of the way."""
        self.search_entry.set_text("")
        self.matches = []
        self.match_idx = -1
        self.match_label.set_text("")
        self._reapply_matches()
        self.find_bar.set_visible(False)
        self.grab_page_focus()

    def _on_search_key(self, ctrl, keyval, code, state):
        if keyval == Gdk.KEY_Escape:
            self.hide_search()
            return True
        return False

    def on_search_changed(self, entry):
        query = entry.get_text()
        self.matches = []
        self.match_idx = -1
        if len(query) < 2:
            self._reapply_matches()
            self.match_label.set_text("")
            return
        needle = query.lower()
        for ci, ch in enumerate(self.chapters):
            hay = "\n".join(t for _, t in ch["blocks"]).lower()
            start = hay.find(needle)
            while start != -1:
                self.matches.append((ci, start))
                start = hay.find(needle, start + 1)
                if len(self.matches) > 4000:
                    break
        self.match_label.set_text(f"0/{len(self.matches)}")
        self._reapply_matches()
        if self.matches:
            self.match_idx = -1
            self.next_match()

    def _reapply_matches(self):
        start, end = self.buffer.get_bounds()
        self.buffer.remove_tag(self.tag_match, start, end)
        self.buffer.remove_tag(self.tag_current, start, end)
        query = self.search_entry.get_text()
        if len(query) < 2:
            return
        it = self.buffer.get_start_iter()
        while True:
            res = it.forward_search(query, Gtk.TextSearchFlags.CASE_INSENSITIVE,
                                    None)
            if not res:
                break
            m_start, m_end = res
            self.buffer.apply_tag(self.tag_match, m_start, m_end)
            it = m_end

    def _goto_match(self):
        if not (0 <= self.match_idx < len(self.matches)):
            return
        ci, _ = self.matches[self.match_idx]
        if ci != self.chapter:
            self.show_chapter(ci)
        self.match_label.set_text(f"{self.match_idx + 1}/{len(self.matches)}")
        # Nth occurrence within this chapter
        nth = sum(1 for j in range(self.match_idx)
                  if self.matches[j][0] == ci)
        query = self.search_entry.get_text()
        it = self.buffer.get_start_iter()
        found = None
        for _ in range(nth + 1):
            res = it.forward_search(query, Gtk.TextSearchFlags.CASE_INSENSITIVE,
                                    None)
            if not res:
                break
            found = res
            it = res[1]
        if found:
            s, e = found
            start, end = self.buffer.get_bounds()
            self.buffer.remove_tag(self.tag_current, start, end)
            self.buffer.apply_tag(self.tag_current, s, e)
            GLib.idle_add(lambda: (self.textview.scroll_to_iter(
                s, 0.15, True, 0.0, 0.35), False)[1])

    def next_match(self):
        if not self.matches:
            return
        self.match_idx = (self.match_idx + 1) % len(self.matches)
        self._goto_match()

    def prev_match(self):
        if not self.matches:
            return
        self.match_idx = (self.match_idx - 1) % len(self.matches)
        self._goto_match()

    def on_open_external(self, btn):
        if self.book:
            subprocess.Popen([self.cfg["open_cmd"], self.book["path"]],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)

    # ------------------------------------------------------------ keys

    def handle_key(self, keyval, state):
        """Reader key handling. True when the key was consumed."""
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if self.search_entry.has_focus() and keyval not in (
                Gdk.KEY_Escape, Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            return False

        line = self.font_size * 2.4

        if keyval == Gdk.KEY_Escape:
            # Escape peels one layer at a time: the find bar, then the
            # contents pane, and only then the book itself.
            if self.find_bar.get_visible():
                self.hide_search()
            elif self.toc_pane.get_visible():
                self.toggle_toc(False)
            else:
                self.close()
        elif keyval in (Gdk.KEY_Down, Gdk.KEY_j):
            self.scroll_by(line)
        elif keyval in (Gdk.KEY_Up, Gdk.KEY_k):
            self.scroll_by(-line)
        elif keyval in (Gdk.KEY_Right, Gdk.KEY_Page_Down, Gdk.KEY_KP_Page_Down):
            self.page(1) if not ctrl else self.next_chapter()
        elif keyval in (Gdk.KEY_Left, Gdk.KEY_Page_Up, Gdk.KEY_KP_Page_Up):
            self.page(-1) if not ctrl else self.prev_chapter()
        elif keyval == Gdk.KEY_space:
            self.page(-1 if shift else 1)
        elif keyval in (Gdk.KEY_n, Gdk.KEY_greater):
            self.next_chapter()
        elif keyval in (Gdk.KEY_p, Gdk.KEY_less):
            self.prev_chapter()
        elif keyval in (Gdk.KEY_Home, Gdk.KEY_KP_Home):
            self._scroll_to_fraction(0.0)
        elif keyval in (Gdk.KEY_End, Gdk.KEY_KP_End):
            self._scroll_to_fraction(1.0)
        elif keyval == Gdk.KEY_t:
            self.toggle_toc()
        elif keyval == Gdk.KEY_slash or (ctrl and keyval in (Gdk.KEY_f, Gdk.KEY_F)):
            self.focus_search()
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.prev_match() if shift else self.next_match()
        elif keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
            self.bump_font(1)
        elif keyval in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
            self.bump_font(-1)
        elif keyval == Gdk.KEY_0 and ctrl:
            self.font_reset()
        elif keyval == Gdk.KEY_o and ctrl:
            self.on_open_external(None)
        else:
            return False
        return True


def human_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def human_ago(stamp):
    if not stamp:
        return "never"
    secs = max(0, int(time.time()) - int(stamp))
    for limit, div, name in ((60, 1, "second"), (3600, 60, "minute"),
                             (86400, 3600, "hour"), (2592000, 86400, "day"),
                             (31536000, 2592000, "month")):
        if secs < limit:
            v = max(1, secs // div)
            return f"{v} {name}{'s' if v != 1 else ''} ago"
    v = max(1, secs // 31536000)
    return f"{v} year{'s' if v != 1 else ''} ago"


class _Card(Gtk.Box):
    """Shared behaviour for the floating cards.

    Both cards sit over a scrim that closes them when it is clicked, and a
    plain box does not consume the clicks that land on its own padding -- so
    without claiming them here, clicking the card's own background dismissed
    the card.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.add_css_class("sw-card")
        claim = Gtk.GestureClick()
        claim.set_button(0)
        claim.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        claim.connect("pressed", lambda g, *a: g.set_state(
            Gtk.EventSequenceState.CLAIMED))
        self.add_controller(claim)

    def _section(self, text):
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.add_css_class("sw-card-section")
        lbl.set_margin_top(6)
        return lbl

    def _row(self, text, widget, subtitle=None):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        names.set_hexpand(True)
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.set_wrap(True)
        names.append(lbl)
        if subtitle:
            sub = Gtk.Label(label=subtitle, xalign=0)
            sub.add_css_class("sw-card-hint")
            sub.set_wrap(True)
            names.append(sub)
        row.append(names)
        widget.set_valign(Gtk.Align.CENTER)
        row.append(widget)
        return row


class SettingsCard(_Card):
    """The one place settings live.

    Nothing is chrome-on-screen any more: neither the shelf nor the reader
    shows a gear, a zoom button or a toolbar. Both surfaces are just the thing
    you came to look at, and this card is summoned by double-clicking and
    dismissed by Escape, its close button, or a click anywhere outside it.
    """

    def __init__(self, cfg, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.cfg = cfg
        self.app = app
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        self.set_size_request(580, -1)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        head.set_margin_top(16)
        head.set_margin_start(22)
        head.set_margin_end(14)
        head.set_margin_bottom(6)
        title = Gtk.Label(label="Bookshelf settings", xalign=0)
        title.add_css_class("sw-card-title")
        title.set_hexpand(True)
        head.append(title)
        # No close button: Escape and a click anywhere outside the card both
        # dismiss it, and a third way to do the same thing is just clutter.
        self.append(head)

        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.body.set_margin_start(22)
        self.body.set_margin_end(22)
        self.body.set_margin_bottom(20)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.body)
        scroller.set_propagate_natural_height(True)
        scroller.set_max_content_height(640)
        self.append(scroller)

        self.rebuild()

    # ------------------------------------------------------------ contents

    def rebuild(self):
        """Repopulate from the live config.

        Settings also arrive over the control socket and from the book cards,
        so the widgets are rebuilt each time the card is shown rather than
        being left displaying whatever was true when it was first built.
        """
        child = self.body.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.body.remove(child)
            child = nxt
        b = self.body

        b.append(self._section("Shelf"))
        b.append(self._display_row())
        b.append(self._sort_row())
        b.append(self._slider("Book size", "scale", 0.6, 1.8, 0.05,
                              self._shelf_changed))
        b.append(self._spin("Mixed: cover-forward every Nth book",
                            "face_out_every", 0, 40, self._shelf_changed))
        b.append(self._switch("Let books lean", "tilt_books",
                              self._shelf_changed,
                              "Books tip into the gap at the end of a shelf. "
                              "Off keeps every book upright."))
        if self.cfg.get("tilt_books", True):
            b.append(self._slider("Lean angle", "tilt_angle", 2.0, 20.0, 0.5,
                                  self._shelf_changed))
        b.append(self._entry("Library folder", "books_dir",
                             self._library_changed))

        b.append(self._section("Wall"))
        b.append(self._wall_row())
        if self.cfg.get("wall_mode") == "wallpaper":
            b.append(self._entry("Wallpaper image", "wallpaper_path",
                                 self._shelf_changed))
            b.append(self._slider("Dim the wallpaper", "wallpaper_dim",
                                  0.0, 0.9, 0.05, self._shelf_changed))

        b.append(self._section("Reader"))
        b.append(self._switch("Full-width text", "reader_full_width",
                              self._reader_changed,
                              "Off caps the line length and centres the column"))
        if not self.cfg.get("reader_full_width", True):
            b.append(self._spin("Line width (characters)", "reader_width",
                                30, 110, self._reader_changed))
        b.append(self._spin("Text size (pt)", "reader_font_size",
                            7, 28, self._reader_changed))
        b.append(self._slider("Line spacing", "reader_line_height",
                              1.0, 2.4, 0.05, self._reader_changed))
        b.append(self._switch("Justify paragraphs", "reader_justify",
                              self._reader_changed))
        b.append(self._entry("Reading font", "reader_font",
                             self._reader_changed))

        if self.app.reader_open():
            b.append(self._section("This book"))
            b.append(self._reader_actions())

        b.append(self._section("Library"))
        b.append(self._library_actions())

        hint = Gtk.Label(
            label="Click a book to open it · double-click anywhere for "
                  "these settings · drag a book to rearrange · "
                  "right-click a book for its details\n"
                  "Esc, or a click outside this card, closes it",
            xalign=0)
        hint.add_css_class("sw-card-hint")
        hint.set_wrap(True)
        b.append(hint)

    # ------------------------------------------------------------ rows

    def _display_row(self):
        """Spines, covers, or a mix -- all of them on the same shelves."""
        box = Gtk.Box(spacing=6)
        current = self.cfg.get("display", "mixed")
        first = None
        for value, label in (("spine", "Spines"), ("cover", "Covers"),
                             ("mixed", "Mixed")):
            btn = Gtk.ToggleButton(label=label)
            if first is None:
                first = btn
            else:
                btn.set_group(first)
            if value == current:
                btn.set_active(True)
            btn.connect("toggled", lambda b, v=value: b.get_active()
                        and self._set("display", v, self._shelf_changed))
            box.append(btn)
        return self._row("Show books as", box,
                         "Individual books can override this from their "
                         "right-click card")

    def _wall_row(self):
        box = Gtk.Box(spacing=6)
        current = self.cfg.get("wall_mode", "color")
        first = None
        for value, label in (("color", "Colour"), ("wallpaper", "Wallpaper")):
            btn = Gtk.ToggleButton(label=label)
            if first is None:
                first = btn
            else:
                btn.set_group(first)
            if value == current:
                btn.set_active(True)
            btn.connect("toggled", lambda b, v=value: b.get_active()
                        and self._set("wall_mode", v, self._wall_changed))
            box.append(btn)
        return self._row("Behind the shelves", box,
                         "Show the desktop wallpaper through the bookcase "
                         "instead of a flat colour")

    def _sort_row(self):
        modes = ["author", "title", "random", "custom"]
        names = ["Author", "Title", "Shuffled", "As arranged"]
        dd = Gtk.DropDown.new_from_strings(names)
        current = self.cfg.get("sort", "author")
        dd.set_selected(modes.index(current) if current in modes else 0)
        dd.connect("notify::selected",
                   lambda d, *_: self._set("sort", modes[d.get_selected()],
                                           self._sort_changed))
        return self._row("Order", dd,
                         "Dragging a book switches this to As arranged")

    def _reader_actions(self):
        box = Gtk.Box(spacing=6)
        for label, fn in (("Contents", lambda: self.app.reader.toggle_toc()),
                          ("Find", lambda: self.app.reader.focus_search()),
                          ("Close book", lambda: self.app.reader.close())):
            btn = Gtk.Button(label=label)
            btn.connect("clicked", lambda b, f=fn: (self.app.hide_settings(),
                                                    f()))
            box.append(btn)
        return self._row("Reading", box,
                         "Also t for contents, / to find, Esc to close")

    def _library_actions(self):
        box = Gtk.Box(spacing=6)
        rescan = Gtk.Button(label="Rescan")
        rescan.connect("clicked", lambda b: self.app.rescan())
        box.append(rescan)
        reset = Gtk.Button(label="Reset arrangement")
        reset.set_tooltip_text("Forget the dragged order and the per-book views")
        reset.connect("clicked", lambda b: self.app.reset_arrangement())
        box.append(reset)
        return self._row("Library", box,
                         f"{len(self.app.books)} book(s) in "
                         f"{self.cfg.get('books_dir', '')}")

    # ------------------------------------------------------------ pieces

    def _slider(self, text, key, lo, hi, step, on_change):
        adj = Gtk.Adjustment(value=float(self.cfg.get(key, lo)), lower=lo,
                             upper=hi, step_increment=step,
                             page_increment=step * 2)
        sl = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj)
        sl.set_digits(2)
        sl.set_size_request(220, -1)
        sl.connect("value-changed",
                   lambda w: self._set(key, round(w.get_value(), 2), on_change))
        return self._row(text, sl)

    def _spin(self, text, key, lo, hi, on_change):
        adj = Gtk.Adjustment(value=float(self.cfg.get(key, lo)), lower=lo,
                             upper=hi, step_increment=1, page_increment=4)
        sp = Gtk.SpinButton(adjustment=adj, climb_rate=1, digits=0)
        sp.connect("value-changed",
                   lambda w: self._set(key, int(w.get_value()), on_change))
        return self._row(text, sp)

    def _switch(self, text, key, on_change, subtitle=None):
        sw = Gtk.Switch()
        sw.set_active(bool(self.cfg.get(key, False)))
        sw.connect("notify::active",
                   lambda w, *_: self._set(key, w.get_active(), on_change))
        return self._row(text, sw, subtitle)

    def _entry(self, text, key, on_change):
        en = Gtk.Entry()
        en.set_text(str(self.cfg.get(key, "")))
        en.set_size_request(260, -1)
        en.connect("activate", lambda w: self._set(key, w.get_text(), on_change))
        en.connect("notify::has-focus",
                   lambda w, *_: (not w.has_focus())
                   and self._set(key, w.get_text(), on_change))
        return self._row(text, en)

    # ------------------------------------------------------------ applying

    def _set(self, key, value, on_change):
        if self.cfg.get(key) == value:
            return
        self.cfg[key] = value
        save_config_soon(self.cfg)
        on_change(key)

    def _shelf_changed(self, key):
        self.app.invalidate_shelves()
        if key in ("tilt_books", "reader_full_width"):
            # These rows govern which other rows are worth showing.
            GLib.idle_add(self.rebuild)

    def _wall_changed(self, key):
        self.app.invalidate_shelves()
        GLib.idle_add(self.rebuild)

    def _sort_changed(self, key):
        self.app.resort()

    def _library_changed(self, key):
        self.cfg["books_dir"] = os.path.expanduser(self.cfg["books_dir"])
        self.app.rescan()

    def _reader_changed(self, key):
        self.app.apply_reader_settings()
        if key == "reader_full_width":
            GLib.idle_add(self.rebuild)


class BookInfoCard(_Card):
    """What this book is, how far into it you are, and how it should stand.

    Right-clicking a book is the only way to say something about one book
    rather than about the shelf, so both halves of that live here: the details
    worth knowing, and the per-book view override.
    """

    def __init__(self, cfg, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.cfg = cfg
        self.app = app
        self.book = None
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.START)
        self.set_size_request(430, -1)

        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.body.set_margin_top(16)
        self.body.set_margin_start(18)
        self.body.set_margin_end(18)
        self.body.set_margin_bottom(16)
        self.append(self.body)

    # ------------------------------------------------------------ contents

    def show_for(self, book, x, y, W, H):
        self.book = book
        self._fill()
        # Placed at the pointer, then pulled back inside the screen. A card
        # that opens half off the edge is worse than one that is not quite
        # where you clicked.
        wid, hei = 430, 340
        self.set_margin_start(int(max(12, min(W - wid - 12, x + 14))))
        self.set_margin_top(int(max(12, min(H - hei - 12, y - 30))))
        self.set_visible(True)

    def _fill(self):
        child = self.body.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.body.remove(child)
            child = nxt
        b, book = self.body, self.book
        if not book:
            return

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        titles.set_hexpand(True)
        title = Gtk.Label(label=book.get("title", ""), xalign=0)
        title.add_css_class("sw-card-title")
        title.set_wrap(True)
        title.set_max_width_chars(34)
        titles.append(title)
        if book.get("author"):
            author = Gtk.Label(label=book["author"], xalign=0)
            author.add_css_class("sw-card-hint")
            author.set_wrap(True)
            titles.append(author)
        head.append(titles)
        b.append(head)

        # ---- reading progress
        rec = load_progress().get(book["path"], {})
        pct = float(rec.get("pct", 0.0))
        if not rec:
            summary = "Not started"
        else:
            chapter = int(rec.get("chapter", 0)) + 1
            total = int(rec.get("chapters", 0) or 0)
            where = (f"chapter {chapter} of {total}" if total
                     else f"chapter {chapter}")
            summary = (f"{pct:.0f}% read · {where} · "
                       f"last opened {human_ago(rec.get('at'))}")
        bar = Gtk.ProgressBar()
        bar.set_fraction(max(0.0, min(1.0, pct / 100.0)))
        bar.set_hexpand(True)
        b.append(bar)
        line = Gtk.Label(label=summary, xalign=0)
        line.add_css_class("sw-card-hint")
        line.set_wrap(True)
        b.append(line)

        # ---- the file
        path = book["path"]
        facts = [f"{human_size(book.get('size'))} · "
                 f"{os.path.basename(path)}",
                 os.path.dirname(path)]
        if not book.get("cover"):
            facts.append("No cover art in this file — it can only be "
                         "shown as a spine")
        for text in facts:
            lbl = Gtk.Label(label=text, xalign=0)
            lbl.add_css_class("sw-card-hint")
            lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            lbl.set_max_width_chars(46)
            b.append(lbl)

        b.append(self._section("Show this book as"))
        b.append(self._state_row())

        actions = Gtk.Box(spacing=6)
        actions.set_margin_top(4)
        open_btn = Gtk.Button(label="Open")
        open_btn.add_css_class("suggested-action")
        open_btn.connect("clicked", lambda w: self.app.open_from_card())
        actions.append(open_btn)
        ext = Gtk.Button(label="Open elsewhere")
        ext.set_tooltip_text(f"Hand the file to {self.cfg.get('open_cmd')}")
        ext.connect("clicked", lambda w: self.app.open_external(book))
        actions.append(ext)
        if rec:
            forget = Gtk.Button(label="Forget progress")
            forget.connect("clicked", lambda w: self.app.forget_progress(book))
            actions.append(forget)
        b.append(actions)

    def _state_row(self):
        box = Gtk.Box(spacing=6)
        states = self.cfg.get("book_states") or {}
        current = states.get(self.book["path"], "auto")
        has_cover = bool(self.book.get("cover"))
        first = None
        for value, label in (("auto", "Auto"), ("cover", "Cover"),
                             ("spine", "Spine"), ("tilt", "Tilted")):
            btn = Gtk.ToggleButton(label=label)
            if first is None:
                first = btn
            else:
                btn.set_group(first)
            if value == "cover" and not has_cover:
                btn.set_sensitive(False)
            if value == current:
                btn.set_active(True)
            btn.connect("toggled", lambda w, v=value: w.get_active()
                        and self.app.set_book_state(self.book, v))
            box.append(btn)
        box.set_halign(Gtk.Align.START)
        return box



# ---------------------------------------------------------------- app

class App(Gtk.Application):
    def __init__(self, cfg, mode, passthrough=False):
        super().__init__(application_id=APP_ID)
        self.cfg = cfg
        self.mode = mode
        self.passthrough = passthrough
        self.books = []
        self.areas = []
        self.shelves = []
        self.layer_windows = []
        self.reader = None
        self.ipc = None
        self.LS = None
        self.click_count = 0
        self.settings_card = None
        self.book_card = None
        self.scrim = None
        self._anim_source = None
        self._reading = False
        self._monitors = []
        # Pointer state. A click on a book has to wait out the double-click
        # window before it opens anything, because the second click of a
        # double means "settings" and the first one must not have opened a
        # book by then.
        self._pending_open = None
        self._double = False
        self._drag = None

    # ------------------------------------------------------------ startup

    def do_activate(self):
        self.books = self._sorted(build_index(self.cfg["books_dir"]))
        if self.mode == "bg":
            self._load_layer_shell()
            display = Gdk.Display.get_default()
            mons = display.get_monitors()
            count = mons.get_n_items()
            for k in range(count):
                self.make_window(monitor=mons.get_item(k), primary=(k == 0))
            if count == 0:
                self.make_window(monitor=None, primary=True)
        else:
            self.make_window(monitor=None, primary=True)
        self.ipc = IPCServer(self.on_ipc)
        self.watch()

    def _load_layer_shell(self):
        try:
            gi.require_version("Gtk4LayerShell", "1.0")
            from gi.repository import Gtk4LayerShell as LS
        except Exception as e:
            print("gtk4-layer-shell missing:", e, file=sys.stderr)
            sys.exit(1)
        self.LS = LS

    def make_window(self, monitor=None, primary=False):
        win = Gtk.ApplicationWindow(application=self)
        win.set_title("shelfwall")
        shelf = Shelf(self.cfg, self.books)
        self.shelves.append(shelf)

        area = Gtk.DrawingArea()
        area.set_hexpand(True)
        area.set_vexpand(True)
        area.set_draw_func(shelf.draw)
        area.set_focusable(True)
        self.areas.append(area)

        overlay = Gtk.Overlay()
        overlay.set_child(area)

        # Interaction is attached in every mode. The wallpaper being a
        # wallpaper is a matter of which layer it lives on, not of whether
        # it responds to the pointer.
        if not self.passthrough:
            # In the capture phase so the press count is known before the drag
            # gesture below decides whether to pick a book up.
            click = Gtk.GestureClick()
            click.set_button(Gdk.BUTTON_PRIMARY)
            click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            click.connect("pressed", self.on_press, shelf)
            area.add_controller(click)

            drag = Gtk.GestureDrag()
            drag.set_button(Gdk.BUTTON_PRIMARY)
            drag.connect("drag-begin", self.on_drag_begin, shelf, area)
            drag.connect("drag-update", self.on_drag_update, shelf, area)
            drag.connect("drag-end", self.on_drag_end, shelf, area)
            area.add_controller(drag)

            secondary = Gtk.GestureClick()
            secondary.set_button(Gdk.BUTTON_SECONDARY)
            secondary.connect("pressed", self.on_secondary, shelf, area)
            area.add_controller(secondary)

            motion = Gtk.EventControllerMotion()
            motion.connect("motion", self.on_motion, shelf, area)
            motion.connect("leave", self.on_leave, shelf, area)
            area.add_controller(motion)

        if primary:
            self.reader = EpubReaderView(self.cfg, on_close=self._on_reader_closed)
            self.reader.set_visible(False)
            overlay.add_overlay(self.reader)

            # Double-clicking the page opens the settings card too, so the
            # reader itself can stay free of controls.
            reader_dbl = Gtk.GestureClick()
            reader_dbl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            reader_dbl.connect(
                "pressed",
                lambda g, n, x, y: n == 2 and self.toggle_settings())
            self.reader.add_controller(reader_dbl)

            # The scrim goes on before the cards so it sits underneath them:
            # a click that reaches it is by definition a click outside the
            # card, which is what dismisses it.
            self.scrim = Gtk.Box()
            self.scrim.add_css_class("sw-scrim")
            self.scrim.set_visible(False)
            scrim_click = Gtk.GestureClick()
            scrim_click.set_button(0)
            scrim_click.connect("pressed",
                                lambda g, n, x, y: self.dismiss_cards())
            self.scrim.add_controller(scrim_click)
            overlay.add_overlay(self.scrim)

            self.settings_card = SettingsCard(self.cfg, self)
            self.settings_card.set_visible(False)
            overlay.add_overlay(self.settings_card)

            self.book_card = BookInfoCard(self.cfg, self)
            self.book_card.set_visible(False)
            overlay.add_overlay(self.book_card)

        win.set_child(overlay)

        key = Gtk.EventControllerKey()
        key.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key.connect("key-pressed", self.on_key)
        win.add_controller(key)

        if self.mode == "bg":
            self._make_layer_surface(win, monitor)
            win.present()
        else:
            win.set_default_size(1400, 900)
            win.present()
            win.maximize()
        return win


    def _make_layer_surface(self, win, monitor):
        LS = self.LS
        LS.init_for_window(win)
        # Hyprland does not deliver pointer events to the background layer at
        # all, so a shelf living there can never be clicked. The bottom layer
        # is the lowest one that is still interactive, and it is equally far
        # below every ordinary window, so this costs nothing visually.
        layers = {"bottom": LS.Layer.BOTTOM, "background": LS.Layer.BACKGROUND,
                  "top": LS.Layer.TOP}
        LS.set_layer(win, layers.get(self.cfg.get("layer_shell", "bottom"),
                                     LS.Layer.BOTTOM))
        try:
            LS.set_namespace(win, "shelfwall")
        except Exception:
            pass
        for edge in (LS.Edge.TOP, LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT):
            LS.set_anchor(win, edge, True)
        LS.set_exclusive_zone(win, -1)
        LS.set_keyboard_mode(
            win, LS.KeyboardMode.NONE if self.passthrough
            else LS.KeyboardMode.ON_DEMAND)
        if monitor:
            LS.set_monitor(win, monitor)
        if self.passthrough:
            win.connect("realize", self._passthrough)
        self.layer_windows.append(win)

    def set_keyboard_mode(self, exclusive):
        """Keyboard interactivity for the wallpaper surface.

        Deliberately never EXCLUSIVE. An exclusive grab taken by a surface
        that sits *behind* every window swallows the keystrokes meant for
        whatever the user is actually looking at, and there is no reliable way
        to notice that and hand them back. ON_DEMAND means clicking the page
        gives it the keyboard and clicking a window takes it away again, which
        is the behaviour you would expect from something living on the desktop.

        Reading without focus is covered by the compositor-level binds, which
        reach shelfwall over its control socket no matter who holds focus.
        """
        if not (self.LS and self.layer_windows) or self.passthrough:
            return
        for win in self.layer_windows:
            try:
                self.LS.set_keyboard_mode(win, self.LS.KeyboardMode.ON_DEMAND)
            except Exception:
                pass

    # ------------------------------------------------------------ reader

    def open_book(self, book):
        if not (book and self.reader):
            return
        self._cancel_pending_open()
        self.hide_book_card()
        self.hide_settings()
        for sh in self.shelves:
            sh.hover = -1
        self.set_keyboard_mode(True)
        self.reader.open(book)
        self._tell_shell(reading=True)

    def _on_reader_closed(self):
        self.set_keyboard_mode(False)
        self._tell_shell(reading=False)
        self.redraw()

    def _tell_shell(self, reading):
        """Let illogical-impulse know whether a book is open.

        This used to ask the shell to hide its bar for the duration. It no
        longer does: the shelf and the reader are the same surface at the same
        size, so taking the bar away for one of them made the whole desktop
        jump every time a book was opened or closed. The shell is still told,
        because other parts of it may want to know; what it does with that is
        its own business.
        """
        if reading == self._reading:
            return
        self._reading = reading
        try:
            subprocess.Popen(
                ["qs", "-c", "ii", "ipc", "call", "bookshelf",
                 "readerOpened" if reading else "readerClosed"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            self._reading = False

    def toggle_reader(self):
        if self.reader and self.reader.get_visible():
            self.reader.close()
            return "closed"
        book = self._last_or_first_book()
        if book:
            self.open_book(book)
            return "opened " + book["title"]
        return "no books"

    def _last_or_first_book(self):
        """The book to resume: most recently read, not most nearly finished."""
        if not self.books:
            return None
        progress = load_progress()
        for path in sorted(progress, key=lambda p: progress[p].get("at", 0),
                           reverse=True):
            book = next((b for b in self.books if b["path"] == path), None)
            if book:
                return book
        return self.books[0]

    # ------------------------------------------------------------ ipc

    def on_ipc(self, line):
        parts = line.split(None, 1)
        cmd = (parts[0] if parts else "").lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        reader_up = bool(self.reader and self.reader.get_visible())

        if cmd in ("ping", "alive"):
            return "shelfwall"
        if cmd == "state":
            return json.dumps({
                "books": len(self.books),
                "reader": reader_up,
                "book": (self.reader.book or {}).get("title") if reader_up else None,
                "chapter": self.reader.chapter if reader_up else None,
                "chapters": len(self.reader.chapters) if reader_up else 0,
                "hover": self.shelves[0].hover if self.shelves else None,
                "clicks": self.click_count,
                "layer": "background" if self.layer_windows else "window",
            })
        if cmd == "quit":
            GLib.idle_add(self.quit)
            return "quitting"
        if cmd == "rescan":
            self.rescan()
            return f"{len(self.books)} books"
        if cmd == "reload":
            self.reload_config()
            return "reloaded"
        if cmd == "set":
            return self.ipc_set(arg)
        if cmd == "get":
            return json.dumps({k: v for k, v in self.cfg.items()
                               if k not in DERIVED_KEYS})
        if cmd == "toggle-reader":
            return self.toggle_reader()
        if cmd == "close-reader":
            if reader_up:
                self.reader.close()
                return "closed"
            return "not open"
        if cmd == "open":
            book = None
            if arg.isdigit():
                idx = int(arg)
                if 0 <= idx < len(self.books):
                    book = self.books[idx]
            else:
                low = arg.lower()
                book = next((b for b in self.books
                             if low in b["path"].lower()
                             or low in b["title"].lower()), None)
            if not book:
                return "no match"
            self.open_book(book)
            return book["title"]
        if cmd == "random":
            if not self.books:
                return "no books"
            self.open_book(random.choice(self.books))
            return "ok"
        if cmd == "settings":
            self.toggle_settings()
            return "ok"
        if cmd == "close-settings":
            self.dismiss_cards()
            return "ok"
        if cmd == "wallpaper":
            # One switch for "shelves over the wallpaper": the app stays
            # exactly where it is, only the wall behind it changes.
            want = {"on": "wallpaper", "off": "color"}.get(arg.lower())
            if want is None:
                want = ("color" if self.cfg.get("wall_mode") == "wallpaper"
                        else "wallpaper")
            return self.ipc_set(f"wall_mode {want}")
        if cmd == "book-state":
            bits = arg.split(None, 1)
            if len(bits) != 2 or bits[0] not in ("auto", "cover", "spine",
                                                 "tilt"):
                return "usage: book-state <auto|cover|spine|tilt> <book>"
            state, low = bits[0], bits[1].strip().lower()
            book = next((b for b in self.books
                         if low in b["path"].lower()
                         or low in b["title"].lower()), None)
            if not book:
                return "no match"
            self.set_book_state(book, state)
            return f'{book["title"]}: {state}'
        if reader_up:
            actions = {
                "next-chapter": self.reader.next_chapter,
                "prev-chapter": self.reader.prev_chapter,
                "page-down": lambda: self.reader.page(1),
                "page-up": lambda: self.reader.page(-1),
                "toc": self.reader.toggle_toc,
                "find": self.reader.focus_search,
            }
            if cmd in actions:
                actions[cmd]()
                return "ok"
        return f"unknown or inapplicable: {cmd}"

    # Settings are pushed in one key at a time rather than by rewriting
    # config.json, so the shell and the in-app panel can both own settings
    # without either clobbering the other's keys.
    IPC_SETTABLE = {
        "books_dir": str, "sort": str, "display": str, "open_cmd": str,
        "reader_font": str, "wall_mode": str, "wallpaper_path": str,
        "scale": float, "reader_line_height": float, "tilt_angle": float,
        "wallpaper_dim": float,
        "face_out_every": int, "reader_width": int, "reader_font_size": int,
        "theme_from_shell": bool, "reader_justify": bool, "tilt_books": bool,
        "reader_full_width": bool,
        "layer_shell": str,
    }

    def ipc_set(self, arg):
        parts = arg.split(None, 1)
        if len(parts) != 2:
            return "usage: set <key> <value>"
        key, raw = parts[0], parts[1].strip()
        caster = self.IPC_SETTABLE.get(key)
        if caster is None:
            return f"not settable: {key}"
        try:
            if caster is bool:
                value = raw.lower() in ("1", "true", "yes", "on")
            else:
                value = caster(raw)
        except ValueError:
            return f"bad value for {key}: {raw}"
        if key == "books_dir":
            value = os.path.expanduser(value)
        if self.cfg.get(key) == value:
            return "unchanged"

        self.cfg[key] = value
        save_config(self.cfg)
        if key == "theme_from_shell":
            self.cfg.update(themed_config(self.cfg))
        if key == "books_dir":
            self.rescan()
        elif key in ("sort", "book_order"):
            self.resort()
        elif key.startswith("reader_"):
            if self.reader:
                self.reader.font_size = float(
                    self.cfg.get("reader_font_size",
                                 READER_DEFAULTS["reader_font_size"]))
                self.reader._apply_css()
                self.reader._relayout()
        else:
            self.invalidate_shelves()
        return f"{key}={value}"

    # ------------------------------------------------------------ settings

    def show_settings(self):
        if not self.settings_card:
            return
        self.hide_book_card()
        # Rebuilt on the way in: settings also change over the control socket
        # and from the book cards, and a card showing stale values is worse
        # than one that takes a moment to appear.
        self.settings_card.rebuild()
        self.settings_card.set_visible(True)
        self._sync_scrim()

    def hide_settings(self):
        if self.settings_card:
            self.settings_card.set_visible(False)
        self._sync_scrim()
        if self.reader_open():
            self.reader.grab_page_focus()

    def toggle_settings(self):
        if not self.settings_card:
            return
        if self.settings_card.get_visible():
            self.hide_settings()
        else:
            self.show_settings()

    def settings_open(self):
        return bool(self.settings_card and self.settings_card.get_visible())

    def reader_open(self):
        return bool(self.reader and self.reader.get_visible())

    def invalidate_shelves(self):
        for sh in self.shelves:
            sh.invalidate()
        self.redraw()

    def _sorted(self, books):
        return sort_books(books, self.cfg.get("sort", "author"),
                          self.cfg.get("book_order"))

    def resort(self):
        self.books = self._sorted(self.books)
        for sh in self.shelves:
            sh.set_books(self.books)
        self.redraw()

    def apply_reader_settings(self):
        if not self.reader:
            return
        self.reader.font_size = float(
            self.cfg.get("reader_font_size",
                         READER_DEFAULTS["reader_font_size"]))
        self.reader._apply_css()
        self.reader._relayout()

    # ------------------------------------------------------------ misc

    @staticmethod
    def _passthrough(win):
        try:
            win.get_surface().set_input_region(cairo.Region())
        except Exception:
            pass

    def _kick_animation(self):
        """Run the hover animation only while something is actually moving.

        The previous version installed a frame-clock tick that fired sixty
        times a second forever, whether or not anything was animating.
        """
        if self._anim_source is None:
            self._anim_source = GLib.timeout_add(16, self._animate)

    def _animate(self):
        alive = False
        for shelf, area in zip(self.shelves, self.areas):
            if shelf.animate_step():
                area.queue_draw()
            if shelf.is_animating():
                alive = True
        if not alive:
            self._anim_source = None
            return False
        return True

    def redraw(self):
        for area in self.areas:
            area.queue_draw()

    def on_key(self, ctrl, keyval, code, state):
        if self.book_card_open():
            if keyval == Gdk.KEY_Escape:
                self.hide_book_card()
                return True
            return False
        if self.settings_open():
            if keyval == Gdk.KEY_Escape:
                self.hide_settings()
                return True
            return False
        if self.reader and self.reader.get_visible():
            return self.reader.handle_key(keyval, state)
        if keyval == Gdk.KEY_Escape:
            # In wallpaper mode there is nothing to quit out of; just drop the
            # keyboard so the next keystroke goes back to the focused window.
            if self.mode == "bg":
                self.set_keyboard_mode(False)
                return True
            self.quit()
            return True
        if keyval == Gdk.KEY_q and self.mode == "window":
            self.quit()
            return True
        if keyval == Gdk.KEY_r:
            self.rescan()
            return True
        if keyval == Gdk.KEY_s:
            self.toggle_settings()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            book = self._last_or_first_book()
            if book:
                self.open_book(book)
            return True
        return False

    def on_motion(self, ctrl, x, y, shelf, area):
        if self.reader_open() or self.settings_open() or self.book_card_open():
            return
        if shelf.drag_path:
            return  # the pointer is carrying a book, not pointing at one
        b = shelf.at(x, y)
        path = b["path"] if b else -1
        if path != shelf.hover:
            shelf.hover = path
            self._kick_animation()

    def on_leave(self, ctrl, shelf, area):
        if shelf.hover != -1:
            shelf.hover = -1
            self._kick_animation()

    # ------------------------------------------------------------ pointer

    DRAG_THRESHOLD = 8.0   # px before a press turns into a rearrange

    def _double_click_ms(self):
        try:
            ms = int(Gtk.Settings.get_default()
                     .get_property("gtk-double-click-time"))
        except Exception:
            ms = 400
        return max(120, min(600, ms))

    def _schedule_open(self, book):
        """Open a book once it is clear no second click is coming."""
        self._cancel_pending_open()
        self._pending_open = GLib.timeout_add(
            self._double_click_ms(), self._fire_pending_open, book)

    def _fire_pending_open(self, book):
        self._pending_open = None
        self.open_book(book)
        return False

    def _cancel_pending_open(self):
        if self._pending_open is not None:
            GLib.source_remove(self._pending_open)
            self._pending_open = None

    def on_press(self, gesture, npress, x, y, shelf):
        self.click_count += 1
        if npress >= 2:
            # The second click of a double is settings, wherever it lands --
            # on a book or on bare shelf. Cancelling the pending open is what
            # stops a double-click on a book from opening the book first,
            # which is exactly what it used to do.
            self._double = True
            self._cancel_pending_open()
            self._cancel_drag(shelf)
            self.toggle_settings()
        else:
            self._double = False

    def on_drag_begin(self, gesture, sx, sy, shelf, area):
        if self._double:
            return
        self._drag = {"x": sx, "y": sy, "shelf": shelf, "area": area,
                      "book": shelf.at(sx, sy), "active": False}

    def on_drag_update(self, gesture, ox, oy, shelf, area):
        d = self._drag
        if not d or self._double:
            return
        if not d["active"]:
            if not d["book"] or math.hypot(ox, oy) < self.DRAG_THRESHOLD:
                return
            # Past the threshold with a book under the press: this is a
            # rearrange, not a click, so whatever the release was going to
            # open is called off.
            d["active"] = True
            self._cancel_pending_open()
            shelf.hover = -1
            shelf.drag_path = d["book"]["path"]
        shelf.drag_xy = (d["x"] + ox, d["y"] + oy)
        shelf.set_drop(d["x"] + ox, d["y"] + oy)
        area.queue_draw()

    def on_drag_end(self, gesture, ox, oy, shelf, area):
        d = self._drag
        self._drag = None
        if not d or self._double:
            self._cancel_drag(shelf)
            return
        if d["active"]:
            self._drop_book(shelf, d["book"], shelf.drop_index)
            return
        if d["book"]:
            self._schedule_open(d["book"])

    def _cancel_drag(self, shelf):
        self._drag = None
        if shelf.drag_path or shelf.drop_mark:
            shelf.clear_drag()
            self.redraw()

    def _drop_book(self, shelf, book, target):
        """Land a dragged book, and remember the arrangement."""
        shelf.clear_drag()
        if not book or target is None:
            self.redraw()
            return
        order = [b["path"] for b in self.books]
        try:
            src = order.index(book["path"])
        except ValueError:
            self.redraw()
            return
        order.pop(src)
        # The caret index was measured against the shelf as it stands, so a
        # book moving rightwards has to account for the slot it just vacated.
        if target > src:
            target -= 1
        order.insert(max(0, min(len(order), target)), book["path"])

        self.cfg["sort"] = "custom"
        self.cfg["book_order"] = order
        save_config(self.cfg)
        self.books = sort_books(self.books, "custom", order)
        for sh in self.shelves:
            sh.set_books(self.books)
        self.redraw()

    def on_secondary(self, gesture, npress, x, y, shelf, area):
        """Right-click: what this book is, and how it should stand."""
        self._cancel_pending_open()
        self._cancel_drag(shelf)
        book = shelf.at(x, y)
        if not book:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.show_book_card(book, x, y, area)

    # ------------------------------------------------------------ book card

    def show_book_card(self, book, x, y, area):
        if not self.book_card:
            return
        self.hide_settings()
        self.book_card.show_for(book, x, y,
                                area.get_width(), area.get_height())
        self._sync_scrim()

    def hide_book_card(self):
        if self.book_card:
            self.book_card.set_visible(False)
        self._sync_scrim()

    def book_card_open(self):
        return bool(self.book_card and self.book_card.get_visible())

    def open_from_card(self):
        book = self.book_card.book if self.book_card else None
        self.hide_book_card()
        if book:
            self.open_book(book)

    def open_external(self, book):
        self.hide_book_card()
        try:
            subprocess.Popen([self.cfg.get("open_cmd", "xdg-open"),
                              book["path"]],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except Exception as e:
            print("open failed:", e, file=sys.stderr)

    def forget_progress(self, book):
        store = load_progress()
        if store.pop(book["path"], None) is not None:
            save_progress(store)
        if self.reader:
            self.reader.progress = load_progress()
        if self.book_card_open():
            self.book_card._fill()

    def set_book_state(self, book, state):
        """Per-book view override: auto, cover, spine or tilt."""
        states = dict(self.cfg.get("book_states") or {})
        if state == "auto":
            states.pop(book["path"], None)
        else:
            states[book["path"]] = state
        if states == (self.cfg.get("book_states") or {}):
            return
        self.cfg["book_states"] = states
        save_config(self.cfg)
        self.invalidate_shelves()

    def reset_arrangement(self):
        self.cfg["book_order"] = []
        self.cfg["book_states"] = {}
        if self.cfg.get("sort") == "custom":
            self.cfg["sort"] = "author"
        save_config(self.cfg)
        self.resort()
        if self.settings_card:
            self.settings_card.rebuild()

    def dismiss_cards(self):
        """Close whatever is open. What a click outside a card means."""
        self.hide_book_card()
        self.hide_settings()

    def _sync_scrim(self):
        if self.scrim:
            self.scrim.set_visible(self.settings_open() or self.book_card_open())


    def rescan(self, *a):
        self.books = self._sorted(build_index(self.cfg["books_dir"]))
        for sh in self.shelves:
            sh.set_books(self.books)
        self.redraw()
        return True

    def reload_config(self, *a, external_only=False):
        """Re-read config.json and the shell palette without restarting."""
        if external_only and config_is_ours():
            return True
        fresh = themed_config(load_config())
        books_dir = self.cfg.get("books_dir")
        self.cfg.clear()
        self.cfg.update(fresh)
        self.apply_reader_settings()
        self.invalidate_shelves()
        if self.cfg.get("books_dir") != books_dir:
            self.rescan()
        else:
            self.books = self._sorted(self.books)
            for sh in self.shelves:
                sh.set_books(self.books)
            self.redraw()
        return True

    def watch(self):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 10, self.rescan)   # SIGUSR1
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 12,
                             self.reload_config)                        # SIGUSR2
        self._monitors = []
        for path, cb, is_dir in (
                (self.cfg["books_dir"], self.rescan, True),
                (str(CONF), lambda: self.reload_config(external_only=True), False),
                (str(SHELL_COLORS), self.reload_config, False)):
            try:
                f = Gio.File.new_for_path(path)
                mon = (f.monitor_directory(Gio.FileMonitorFlags.NONE, None)
                       if is_dir else
                       f.monitor_file(Gio.FileMonitorFlags.NONE, None))
                mon.connect("changed", lambda *a, _cb=cb: _cb())
                self._monitors.append(mon)
            except Exception:
                pass

    def do_shutdown(self):
        flush_pending_save()
        if self.reader and self.reader.book:
            self.reader.remember()
        if self.ipc:
            self.ipc.shutdown()
        Gtk.Application.do_shutdown(self)


def main():
    ap = argparse.ArgumentParser(
        prog="shelfwall",
        epilog="shelfwall ctl <command>   talk to the running instance "
               "(toggle-reader, close-reader, open <name>, random, rescan, "
               "reload, settings, next-chapter, prev-chapter, quit)")
    ap.add_argument("--mode", choices=["bg", "window"], default="bg",
                    help="bg: interactive wallpaper layer (default). "
                         "window: ordinary window, for debugging")
    ap.add_argument("--dir")
    ap.add_argument("--scale", type=float)
    ap.add_argument("--passthrough", action="store_true",
                    help="click-through wallpaper, no interaction at all")
    ap.add_argument("--rescan", action="store_true",
                    help="rebuild the cover/metadata cache and exit")
    args = ap.parse_args()

    cfg = themed_config(load_config(args.dir, args.scale))
    if args.rescan:
        books = build_index(cfg["books_dir"], force=True)
        covers = sum(1 for b in books if b.get("cover"))
        print(f"indexed {len(books)} epub(s), {covers} with covers, "
              f"{len(books) - covers} without", file=sys.stderr)
        sys.exit(0)

    app = App(cfg, args.mode, passthrough=args.passthrough)
    sys.exit(app.run([]))


if __name__ == "__main__":
    main()
