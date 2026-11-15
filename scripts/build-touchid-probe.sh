#!/usr/bin/env bash
# Reads the installed boot DTB; no installation, boot selection or device access.
set -euo pipefail
if (( $# != 1 )) || [[ $1 != --build && $1 != --build-sensor && $1 != --build-touchid && $1 != --build-touchid-sco ]]; then
  echo "Usage: $0 {--build|--build-sensor|--build-touchid|--build-touchid-sco}" >&2
  exit 2
fi
case $1 in
  --build) variant=sep-probe ;;
  --build-sensor) variant=sep-sensor ;;
  --build-touchid) variant=touchid ;;
  --build-touchid-sco) variant=touchid-sco ;;
esac
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
[[ $(uname -m) == aarch64 ]] || { echo 'Native ARM64 host required' >&2; exit 1; }
[[ ${JOBS:-6} =~ ^[1-9][0-9]*$ ]] || { echo 'JOBS must be a positive integer' >&2; exit 2; }
out=$root/outputs/kernel-$variant-candidate
[[ ! -e "$out" ]] || { echo "Refusing existing output: $out" >&2; exit 1; }
commit=8ab10100a4c1c944797e3fc8345589aff57777df
printf '%s  %s\n' 06acab85a9e668010fe9e664d9fc92a15d506a986faab8b9659edd4bbcaaeac2 "$root/work/sources/kernel-$commit.tar.gz" | sha256sum -c -
# Touch ID variants preserve bringup's exact config and sensor DT contract.
if [[ $variant == touchid || $variant == touchid-sco ]]; then
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
sudo -n cp /boot/aurora/root/t6050-j716s.dtb "$out/baseline-boot.dtb"
sudo -n chmod a+r "$out/baseline-boot.dtb"
sudo -n docker run --rm --name "j716s-$variant-kernel-build" --platform linux/arm64 \
  --env JOBS="${JOBS:-6}" --env PROBE_VARIANT="$variant" --env PATCHES="$patches" \
  --env CONFIG="$config" --env CONFIG_SHA256="$config_sha256" --volume "$root:/exchange:ro" --volume "$out:/candidate" \
  docker.io/library/fedora:43 bash -euc '
  dnf -q install -y clang-21.1.8 clang-devel-21.1.8 clang-libs-21.1.8 lld-21.1.8 llvm-21.1.8 make flex bison bc openssl openssl-devel elfutils-libelf-devel python3 git perl gcc rustup findutils diffutils kmod cpio gzip zstd patch tar curl pkgconf-pkg-config ncurses-devel dtc
  rustup-init -y -q --default-toolchain 1.99.1 --profile minimal -c rust-src -c rustfmt -c clippy
  source /root/.cargo/env
  if [[ $PROBE_VARIANT != sep-probe ]]; then
    python3 -B -m unittest discover -s /exchange/tests -p test_sep_sensor_kernel.py -v
  fi
  [[ $(uname -m) == aarch64 && $(rpm -E "%{fedora}") == 43 ]]
  [[ $(clang -dumpversion) == 21.1.8 ]]
  [[ $(rustc --version) == "rustc 1.99.1 "* ]]
  commit=8ab10100a4c1c944797e3fc8345589aff57777df
  archive=/exchange/work/sources/kernel-$commit.tar.gz
  printf "%s  %s\n" 06acab85a9e668010fe9e664d9fc92a15d506a986faab8b9659edd4bbcaaeac2 "$archive" | sha256sum -c -
  mkdir -p /src/sep-probe-linux /build/sep-probe /module-stage
  tar -xzf "$archive" --strip-components=1 -C /src/sep-probe-linux
  cd /src/sep-probe-linux
  for name in $PATCHES; do
    patch --batch -p1 --fuzz=0 < "/exchange/patches/$name"
  done
  python3 tools/aurora-bindgen/build.py
  export BINDGEN=/src/sep-probe-linux/tools/aurora-bindgen/build/bin/bindgen
  export KBUILD_BUILD_USER=codex KBUILD_BUILD_HOST=j716s-kernel-build
  export KBUILD_BUILD_TIMESTAMP="Thu Nov 12 00:00:00 UTC 2026" KBUILD_BUILD_VERSION=1
  common=(LLVM=1 ARCH=arm64 O=/build/sep-probe LOCALVERSION="-j716s-8ab10100a4c1-$PROBE_VARIANT")
  cp "/exchange/$CONFIG" /build/sep-probe/.config
  printf "%s  %s\n" "$CONFIG_SHA256" /build/sep-probe/.config | sha256sum -c -
  make "${common[@]}" rustavailable
  make "${common[@]}" olddefconfig
  cmp /build/sep-probe/.config "/exchange/$CONFIG"
  nice -n 10 make -j"$JOBS" "${common[@]}" Image.gz apple/t6050-j716s.dtb modules
  cmp /build/sep-probe/.config "/exchange/$CONFIG"
  make "${common[@]}" INSTALL_MOD_PATH=/module-stage modules_install
  [[ $(cat /build/sep-probe/include/config/kernel.release) == "7.3.0-j716s-8ab10100a4c1-$PROBE_VARIANT" ]]
  cp /build/sep-probe/arch/arm64/boot/Image.gz /build/sep-probe/arch/arm64/boot/Image /build/sep-probe/arch/arm64/boot/dts/apple/t6050-j716s.dtb /build/sep-probe/.config /build/sep-probe/System.map /build/sep-probe/Module.symvers /candidate/
  cp /build/sep-probe/include/config/kernel.release /candidate/kernel.release
  tar --exclude=build --exclude=source -C /module-stage -czf /candidate/modules.tar.gz lib
  python3 - <<"PY"
import hashlib, importlib.util, json, os, pathlib, subprocess
out = pathlib.Path("/candidate")
def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
helper = pathlib.Path("/exchange/scripts/prepare-sep-probe-dtb.py")
spec = importlib.util.spec_from_file_location("kis_safety", helper)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
dtb_transform = module.prepare(out / "t6050-j716s.dtb", out / "baseline-boot.dtb")
names = ("Image", "Image.gz", "t6050-j716s.dtb", ".config", "System.map", "Module.symvers", "kernel.release", "modules.tar.gz")
artifacts = {name: {"sha256": digest(out / name), "bytes": (out / name).stat().st_size} for name in names}
variant = os.environ["PROBE_VARIANT"]
patches = {name: digest(pathlib.Path("/exchange/patches") / name) for name in os.environ["PATCHES"].split()}
manifest = {"schema": 1, "commit": "8ab10100a4c1c944797e3fc8345589aff57777df", "variant": variant, "qualification": "unqualified-experimental", "kernel_release": (out / "kernel.release").read_text().strip(), "artifacts": artifacts, "patches": patches}
if variant in ("touchid", "touchid-sco"):
    # Runtime options come from modprobe.d; the profile refuses trusted keys, the FileVault API and an unguarded xART.
    safety = {"persistent_selection_allowed": True, "sensor_enabled": True, "enrollment_allowed": True, "matching_allowed": True,
              "required_module_options": {"xart_writes": 1, "provision_keybag": 1, "trusted_keys": 0, "filevault_api": 0, "xart_guard": 1}}
else:
    safety = {"discovery_only": variant == "sep-probe", "sensor_auth_only": variant == "sep-sensor", "persistent_selection_allowed": False, "sensor_enabled": variant == "sep-sensor", "enrollment_allowed": False, "matching_allowed": False, "xart_writes": 0, "provision_keybag": 0, "trusted_keys": 0, "filevault_api": 0, "keystore_only": 1, "xart_guard": 2}
provenance = {"archive_sha256": "06acab85a9e668010fe9e664d9fc92a15d506a986faab8b9659edd4bbcaaeac2", "baseline_config_sha256": os.environ["CONFIG_SHA256"], "builder_sha256": digest(pathlib.Path("/exchange/scripts/build-touchid-probe.sh")), "toolchain": {"guest": "Fedora 43 ARM64", "clang": "21.1.8", "rust": "1.99.1"}, "dtb_transform": dtb_transform, "safety": safety}
(out / "build-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
(out / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
lines = [record["sha256"] + "  " + name + "\n" for name, record in artifacts.items()]
lines.append(digest(out / "build-manifest.json") + "  build-manifest.json\n")
lines.append(digest(out / "build-provenance.json") + "  build-provenance.json\n")
(out / "SHA256SUMS").write_text("".join(lines))
PY
  chmod -R a+rX /candidate
'
