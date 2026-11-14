#!/usr/bin/env bash
# Run only inside the Fedora 43 ARM64 build guest; private signing keys never leave /build.
set -euo pipefail
if (( $# != 1 )) || [[ $1 != baseline && $1 != open-wifi ]]; then
  echo "Usage: $0 {baseline|open-wifi}" >&2
  exit 2
fi
mode=$1
localversion=-j716s-8ab10100a4c1
out=/exchange/outputs/kernel-candidate
patch_sha256=
if [[ $mode == open-wifi ]]; then
  localversion+=-open-wifi
  out=/exchange/outputs/kernel-open-wifi-candidate
fi
source /root/.cargo/env
[[ $(uname -m) == aarch64 && $(rpm -E '%{fedora}') == 43 ]]
[[ $(clang -dumpversion) == 21.1.8 ]]
[[ $(rustc --version) == 'rustc 1.99.1 '* ]]
mkdir -p /src/linux /build /module-stage
commit=8ab10100a4c1c944797e3fc8345589aff57777df
archive=/exchange/work/sources/kernel-$commit.tar.gz
printf '%s  %s\n' 06acab85a9e668010fe9e664d9fc92a15d506a986faab8b9659edd4bbcaaeac2 "$archive" | sha256sum -c -
tar -xzf "$archive" --strip-components=1 -C /src/linux
cd /src/linux
if [[ $mode == open-wifi ]]; then
  candidate_patch=/exchange/patches/kernel-j716s-open-wifi.patch
  patch_sha256=$(sha256sum "$candidate_patch")
  patch_sha256=${patch_sha256%% *}
  patch -p1 --fuzz=0 < "$candidate_patch"
fi
python3 tools/aurora-bindgen/build.py
export BINDGEN=/src/linux/tools/aurora-bindgen/build/bin/bindgen
export KBUILD_BUILD_USER=codex KBUILD_BUILD_HOST=j716s-kernel-build
export KBUILD_BUILD_TIMESTAMP='Thu Nov 12 00:00:00 UTC 2026' KBUILD_BUILD_VERSION=1
common=(LLVM=1 ARCH=arm64 O=/build LOCALVERSION="$localversion")
mkdir -p "$out"
make "${common[@]}" rustavailable
make "${common[@]}" defconfig asahi.config j716s.config j716s-gpu.config
make -j"${JOBS:-8}" "${common[@]}" Image.gz apple/t6050-j716s.dtb modules
make "${common[@]}" INSTALL_MOD_PATH=/module-stage modules_install
[[ $(cat /build/include/config/kernel.release) == "7.3.0$localversion" ]]
cp /build/arch/arm64/boot/Image.gz /build/arch/arm64/boot/Image /build/arch/arm64/boot/dts/apple/t6050-j716s.dtb /build/.config /build/System.map /build/Module.symvers "$out/"
cp /build/include/config/kernel.release "$out/kernel.release"
tar --exclude=build --exclude=source -C /module-stage -czf "$out/modules.tar.gz" lib
python3 - "$out" "$commit" "$mode" "$patch_sha256" <<'PY'
import hashlib, json, pathlib, sys
out = pathlib.Path(sys.argv[1])
names = ('Image', 'Image.gz', 't6050-j716s.dtb', '.config', 'System.map',
         'Module.symvers', 'kernel.release', 'modules.tar.gz')
artifacts = {}
for name in names:
    path = out / name
    with path.open('rb') as stream:
        sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    artifacts[name] = {'sha256': sha, 'bytes': path.stat().st_size}
(out / 'SHA256SUMS').write_text(''.join(f"{item['sha256']}  {name}\n" for name, item in artifacts.items()))
manifest = {'schema': 1, 'commit': sys.argv[2],
    'kernel_release': (out / 'kernel.release').read_text().strip(), 'artifacts': artifacts}
if sys.argv[3] == 'open-wifi':
    manifest.update({
        'variant': 'open-wifi',
        'qualification': 'unqualified-experimental',
        'patches': {'kernel-j716s-open-wifi.patch': sys.argv[4]},
    })
(out / 'build-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
PY
