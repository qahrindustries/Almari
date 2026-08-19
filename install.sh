#!/usr/bin/env bash
# Copyright 2026 Qahr Industries
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# Install Almari into ~/.local/bin.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bin="${HOME}/.local/bin"
conf="${XDG_CONFIG_HOME:-$HOME/.config}/almari"

# Re-running this to pick up a new version should not ask for a password,
# so the package manager is only involved when something is actually
# missing.
have_deps() {
    python3 - <<'DEPS' >/dev/null 2>&1
import ctypes
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: F401
import cairo  # noqa: F401
ctypes.CDLL("libgtk4-layer-shell.so.0")
DEPS
}

if have_deps; then
    echo "dependencies: already present"
elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -S --needed --noconfirm \
        gtk4 gtk4-layer-shell python-gobject python-cairo
else
    echo "Not an Arch system: install gtk4, gtk4-layer-shell, python-gobject" \
         "and python-cairo yourself, then re-run this." >&2
    exit 1
fi

data="${XDG_DATA_HOME:-$HOME/.local/share}"

mkdir -p "$bin" "$conf" "$data/almari" "$data/applications" \
         "$data/icons/hicolor/256x256/apps"
install -m755 "$here/almari.py" "$bin/almari"

# Branding: the mark the settings card looks for at runtime, the icon a
# launcher shows, and the entry that points at both.
install -m644 "$here/assets/logo.png" "$data/almari/logo.png"
install -m644 "$here/packaging/almari.desktop" "$data/applications/almari.desktop"
python3 - "$here/assets/logo.png" "$data/icons/hicolor/256x256/apps/almari.png" <<'SCALE'
import sys
import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf
src, dst = sys.argv[1], sys.argv[2]
GdkPixbuf.Pixbuf.new_from_file_at_scale(src, 256, 256, True).savev(dst, "png", [], [])
SCALE
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -qtf "$data/icons/hicolor" 2>/dev/null || true
fi

# An existing config is never overwritten -- it is where every setting lives.
if [ ! -f "$conf/config.json" ]; then
    install -m644 "$here/config.example.json" "$conf/config.json"
    echo "wrote $conf/config.json — set books_dir in it, or pass --dir"
fi

echo "installed: $bin/almari"
case ":$PATH:" in
    *":$bin:"*) ;;
    *) echo "note: $bin is not on your PATH" >&2 ;;
esac
echo
echo "try:  almari --dir ~/Books --mode bg"
