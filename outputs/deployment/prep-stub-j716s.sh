#!/bin/bash
# SPDX-License-Identifier: MIT
# Fresh J716s preparation entrypoint; default is read-only, --apply requires sudo.
# The Python implementation retains the pinned public disk/stub operations while
# selecting every source/target through plist metadata and persistent UUIDs.
set -euo pipefail
exec python3 -B "$(cd "$(dirname "$0")" && pwd)/prep-stub-j716s.py" "$@"
