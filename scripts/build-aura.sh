#!/usr/bin/env bash
# Host launcher: builds Stage 1 and qualified-source ESP Stage 2, with no target writes.
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
python3 "$root/scripts/prepare-sources.py" --check
runtime=${CONTAINER_RUNTIME:-}
if [[ -z "$runtime" ]]; then
  for candidate in container podman docker; do
    if command -v "$candidate" >/dev/null; then runtime=$candidate; break; fi
  done
fi
[[ "$runtime" == container || "$runtime" == podman || "$runtime" == docker ]] || { echo 'Set CONTAINER_RUNTIME=container, podman, or docker' >&2; exit 1; }
"$runtime" run --rm --name j716s-publication-aura-build --platform linux/arm64 --cpus 4 --memory 8g --volume "$root:/exchange" docker.io/library/fedora:43 bash -euc '
  dnf -q install -y clang-21.1.8 lld-21.1.8 llvm-21.1.8 make python3 git gzip file rustup gcc
  rustup-init -y -q --default-toolchain 1.99.1 --profile minimal -c rust-src
  bash /exchange/work/aura-audit/build-aura-integration.sh
'
