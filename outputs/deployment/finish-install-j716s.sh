#!/bin/bash
# SPDX-License-Identifier: MIT
# Local explicit-identity derivative; default read-only. See --help.
set -euo pipefail
case "$0" in
    */*) HERE=$(cd "${0%/*}" && pwd -P) ;;
    *) HERE=$(pwd -P) ;;
esac
exec python3 -B "$HERE/finish-install-j716s.py" finish "$@"
