"""Shared test scaffolding: load shelfwall.py as a module, and score checks."""

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load():
    spec = importlib.util.spec_from_file_location(
        "shelfwall", os.path.join(ROOT, "shelfwall.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
