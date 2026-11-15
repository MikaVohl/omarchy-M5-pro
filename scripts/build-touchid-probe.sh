#!/usr/bin/env bash
# Reads the installed boot DTB; no installation, boot selection or device access.
set -euo pipefail
if (( $# != 1 )) || [[ $1 != --build && $1 != --build-sensor && $1 != --build-touchid && $1 != --build-touchid-sco && $1 != --build-touchid-sco-spk && $1 != --build-touchid-sco-spk-hfp && $1 != --build-bringup-full && $1 != --build-ane-observe && $1 != --build-ane-start ]]; then
  echo "Usage: $0 {--build|--build-sensor|--build-touchid|--build-touchid-sco|--build-touchid-sco-spk|--build-touchid-sco-spk-hfp|--build-bringup-full|--build-ane-observe|--build-ane-start}" >&2
  exit 2
fi
case $1 in
  --build) variant=sep-probe ;;
  --build-sensor) variant=sep-sensor ;;
  --build-touchid) variant=touchid ;;
  --build-touchid-sco) variant=touchid-sco ;;
  --build-touchid-sco-spk) variant=touchid-sco-spk ;;
  --build-touchid-sco-spk-hfp) variant=touchid-sco-spk-hfp ;;
  --build-bringup-full) variant=bringup-full ;;
  --build-ane-observe) variant=bringup-ane-observe ;;
  --build-ane-start) variant=bringup-ane-start ;;
esac
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
[[ $(uname -m) == aarch64 ]] || { echo 'Native ARM64 host required' >&2; exit 1; }
[[ ${JOBS:-6} =~ ^[1-9][0-9]*$ ]] || { echo 'JOBS must be a positive integer' >&2; exit 2; }
out=$root/outputs/kernel-$variant-candidate
[[ ! -e "$out" ]] || { echo "Refusing existing output: $out" >&2; exit 1; }
commit=8ab10100a4c1c944797e3fc8345589aff57777df
printf '%s  %s\n' 06acab85a9e668010fe9e664d9fc92a15d506a986faab8b9659edd4bbcaaeac2 "$root/work/sources/kernel-$commit.tar.gz" | sha256sum -c -
# Touch ID variants preserve bringup's exact config and sensor DT contract.
if [[ $variant == touchid || $variant == touchid-sco || $variant == touchid-sco-spk || $variant == touchid-sco-spk-hfp || $variant == bringup-full || $variant == bringup-ane-observe || $variant == bringup-ane-start ]]; then
  config=outputs/kernel-bringup-candidate/.config
  config_sha256=e7ff6452a937de895bf351f8539636cbf98998493835b80113da5d5ca4ecba14
else
  config=outputs/kernel-open-wifi-candidate/.config
  config_sha256=74a2fef4c7c36ec82cf3dd3849bc7a672f56823b48f1ef29fa1e359abdc2dd47
fi
printf '%s  %s\n' "$config_sha256" "$root/$config" | sha256sum -c -
# Patches, in application order, are exactly the runtime validator's pins.
patches=$(python3 -B - "$root" "$variant" <<'PY'
import hashlib, importlib.util, pathlib, sys
root, variant = pathlib.Path(sys.argv[1]), sys.argv[2]
spec = importlib.util.spec_from_file_location('candidate', root / 'outputs/persistent-install/candidate_boot.py')
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)
pins = candidate.VARIANTS[variant]['patches']
for name, expected in pins.items():
    if hashlib.sha256((root / 'patches' / name).read_bytes()).hexdigest() != expected:
        raise SystemExit(name + ' differs from runtime pin')
print(' '.join(pins))
PY
)
sudo -n docker info >/dev/null
mkdir "$out"
baseline=$(mktemp /tmp/j716s-baseline.XXXXXXXX)
trap 'rm -f "$baseline"' EXIT
sudo -n cp /boot/aurora/root/t6050-j716s.dtb "$baseline"
sudo -n docker run --rm --name "j716s-$variant-kernel-build" --platform linux/arm64 \
  --env ROOT=/exchange --env OUT=/candidate --env BUILD_HOST_ARCH=aarch64 \
  --env JOBS="${JOBS:-6}" --env PROBE_VARIANT="$variant" --env PATCHES="$patches" \
  --env CONFIG="$config" --env CONFIG_SHA256="$config_sha256" \
  --volume "$root:/exchange:ro" --volume "$out:/candidate" \
  docker.io/library/fedora:43 bash /exchange/scripts/kernel-build-worker.sh
sudo -n chown -R "$(id -u):$(id -g)" "$out"
python3 -B "$root/scripts/finalize-kernel-candidate.py" --variant "$variant" --output "$out" \
  --baseline-dtb "$baseline" --worker-receipt "$out/compile-receipt.json" --builder-arch aarch64
