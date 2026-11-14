#!/bin/bash
# SPDX-License-Identifier: MIT
# Fill pinned raw Aura and its receipt in a local workspace, binding the real ESP GUID.
# No ESP writes or enrollment. Default --check creates nothing; --apply creates the local candidate.
set -euo pipefail
case "$0" in
    */*) HERE=$(cd "${0%/*}" && pwd -P) ;;
    *) HERE=$(pwd -P) ;;
esac
exec python3 -B "$HERE/finish-install-j716s.py" prepare "$@"
