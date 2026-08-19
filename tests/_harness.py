"""Shared test scaffolding: load almari.py, find a library, score checks."""

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load():
    spec = importlib.util.spec_from_file_location(
        "almari", os.path.join(ROOT, "almari.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def library():
    """A folder with epubs in it, for tests that need a real book.

    The developer's own shelf when there is one, and a generated book
    otherwise, so a fresh checkout and a CI runner can both run the suite
    without anybody's library being a prerequisite.
    """
    import json
    import tempfile
    from _epub import write_epub

    conf = os.path.join(
        os.environ.get("XDG_CONFIG_HOME",
                       os.path.expanduser("~/.config")), "almari", "config.json")
    try:
        with open(conf) as fh:
            mine = os.path.expanduser(json.load(fh).get("books_dir", ""))
        if mine and any(f.endswith(".epub")
                        for _, _, fs in os.walk(mine) for f in fs):
            return mine
    except Exception:
        pass
    made = tempfile.mkdtemp(prefix="almari-books-")
    write_epub(os.path.join(made, "A Test Of Patience.epub"))
    write_epub(os.path.join(made, "Second Thoughts.epub"),
               title="Second Thoughts", author="Someone Else", chapters=3)
    return made


class Checks:
    def __init__(self):
        self.failed = []

    def __call__(self, name, cond, extra=""):
        print(("PASS " if cond else "FAIL ") + name
              + ("" if cond else "  " + str(extra)))
        if not cond:
            self.failed.append(name)

    def done(self):
        print()
        print("FAILURES:", self.failed if self.failed else "none")
        sys.exit(1 if self.failed else 0)
