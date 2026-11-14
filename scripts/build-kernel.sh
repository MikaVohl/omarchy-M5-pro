#!/usr/bin/env bash
# Host launcher: creates a disposable build guest, never touches target hardware.
set -euo pipefail
mode=baseline
if (( $# == 1 )) && [[ $1 == --open-wifi ]]; then
  mode=open-wifi
elif (( $# != 0 )); then
  echo "Usage: $0 [--open-wifi]" >&2
  exit 2
fi
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
python3 "$root/scripts/prepare-sources.py" --check
runtime=${CONTAINER_RUNTIME:-}
if [[ -z "$runtime" ]]; then
  for candidate in container podman docker; do
    if command -v "$candidate" >/dev/null; then runtime=$candidate; break; fi
  done
fi
[[ "$runtime" == container || "$runtime" == podman || "$runtime" == docker ]] || { echo 'Set CONTAINER_RUNTIME=container, podman, or docker' >&2; exit 1; }
name=j716s-publication-kernel-build
args=(run --rm --name "$name" --platform linux/arm64)
# Some bring-up kernels lack the CPU/memory cgroup controllers.
if [[ ${BUILD_RESOURCE_LIMITS:-1} != 0 ]]; then
  args+=(--cpus 8 --memory 12g)
fi
"$runtime" "${args[@]}" --volume "$root:/exchange" docker.io/library/fedora:43 bash -euc '
  dnf -q install -y clang-21.1.8 clang-devel-21.1.8 clang-libs-21.1.8 lld-21.1.8 llvm-21.1.8 make flex bison bc openssl openssl-devel elfutils-libelf-devel python3 git perl gcc rustup findutils diffutils kmod cpio gzip zstd patch tar curl pkgconf-pkg-config ncurses-devel
  rustup-init -y -q --default-toolchain 1.99.1 --profile minimal -c rust-src -c rustfmt -c clippy
  bash /exchange/work/kernel-build/build-kernel.sh "$1"
' -- "$mode"
