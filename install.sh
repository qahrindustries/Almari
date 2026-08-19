#!/usr/bin/env bash
# Install shelfwall into ~/.local/bin.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bin="${HOME}/.local/bin"
conf="${XDG_CONFIG_HOME:-$HOME/.config}/shelfwall"

if command -v pacman >/dev/null 2>&1; then
    sudo pacman -S --needed --noconfirm \
        gtk4 gtk4-layer-shell python-gobject python-cairo
else
    echo "Not an Arch system: install gtk4, gtk4-layer-shell, python-gobject" \
         "and python-cairo yourself, then re-run this." >&2
fi

mkdir -p "$bin" "$conf"
install -m755 "$here/shelfwall.py" "$bin/shelfwall"

# An existing config is never overwritten -- it is where every setting lives.
if [ ! -f "$conf/config.json" ]; then
    install -m644 "$here/config.example.json" "$conf/config.json"
    echo "wrote $conf/config.json — set books_dir in it, or pass --dir"
fi

echo "installed: $bin/shelfwall"
case ":$PATH:" in
    *":$bin:"*) ;;
    *) echo "note: $bin is not on your PATH" >&2 ;;
esac
echo
echo "try:  shelfwall --dir ~/Books --mode bg"
