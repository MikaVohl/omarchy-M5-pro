#!/usr/bin/env bash
# Build only in a disposable Fedora ARM64 guest; no target hardware operations.
set -euo pipefail
(( $# == 0 )) || { echo "Usage: $0" >&2; exit 2; }
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
runtime=${CONTAINER_RUNTIME:-}
if [[ -z "$runtime" ]]; then
  for candidate in container podman docker; do
    if command -v "$candidate" >/dev/null; then runtime=$candidate; break; fi
  done
fi
[[ "$runtime" == container || "$runtime" == podman || "$runtime" == docker ]] || { echo 'Set CONTAINER_RUNTIME=container, podman, or docker' >&2; exit 1; }
"$runtime" run --rm --name j716s-avd-firmware-build --platform linux/arm64 --volume "$root:/exchange" docker.io/library/fedora:43 bash -euc '
  dnf -q install -y clang-21.1.8 lld-21.1.8 llvm-21.1.8 make gcc python3 diffutils
  cd /exchange
  python3 -B scripts/prepare-avd-firmware.py
'
