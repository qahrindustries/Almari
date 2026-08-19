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
#
# Run the whole suite. Pass an epub to test the reader against a real book;
# without one it generates its own.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
book="${1:-}"
failed=0

for t in "$here"/test_*.py; do
    name="$(basename "$t")"
    printf '\n=== %s\n' "$name"
    if [ "$name" = "test_reader.py" ] && [ -n "$book" ]; then
        python3 "$t" "$book" || failed=1
    else
        python3 "$t" || failed=1
    fi
done

printf '\n'
if [ "$failed" -eq 0 ]; then
    echo "all suites passed"
else
    echo "some suites failed" >&2
fi
exit "$failed"
