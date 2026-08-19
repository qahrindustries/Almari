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

if command -v pacman >/dev/null 2>&1; then
    sudo pacman -S --needed --noconfirm \
        gtk4 gtk4-layer-shell python-gobject python-cairo
else
    echo "Not an Arch system: install gtk4, gtk4-layer-shell, python-gobject" \
         "and python-cairo yourself, then re-run this." >&2
fi

mkdir -p "$bin" "$conf"
install -m755 "$here/almari.py" "$bin/almari"

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
