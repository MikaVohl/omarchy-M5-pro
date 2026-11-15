#!/usr/bin/env bash
# CPU-only Fedora 43 worker; DTB transformation and boot manifests are local finalizer work.
# ROOT/OUT/CONFIG/CONFIG_SHA256/PATCHES/PROBE_VARIANT/JOBS/BUILD_HOST_ARCH are required.
# SRC, BUILD and MODULE_STAGE optionally name dedicated, persistent cache directories.
set -euo pipefail
[[ $# == 0 ]] || { echo 'kernel-build-worker.sh accepts environment only' >&2; exit 2; }
: "${ROOT:?}" "${OUT:?}" "${CONFIG:?}" "${CONFIG_SHA256:?}" "${PATCHES:?}" "${PROBE_VARIANT:?}" "${JOBS:?}" "${BUILD_HOST_ARCH:?}"
[[ $JOBS =~ ^[1-9][0-9]*$ ]] || { echo 'JOBS must be a positive integer' >&2; exit 2; }
case $BUILD_HOST_ARCH in
  x86_64|aarch64) ;;
  *) echo 'BUILD_HOST_ARCH must be x86_64 or aarch64' >&2; exit 2 ;;
esac
[[ $(uname -m) == "$BUILD_HOST_ARCH" ]] || { echo 'Native builder architecture required; no emulation' >&2; exit 1; }
[[ $(rpm -E '%{fedora}') == 43 ]] || { echo 'Fedora 43 builder required' >&2; exit 1; }
export ROOT OUT CONFIG CONFIG_SHA256 PATCHES PROBE_VARIANT JOBS BUILD_HOST_ARCH
export SRC=${SRC:-/src/sep-probe-linux} BUILD=${BUILD:-/build/sep-probe} MODULE_STAGE=${MODULE_STAGE:-/module-stage}
worker=$(realpath -- "${BASH_SOURCE[0]}")
export WORKER_PATH=$worker
command -v python3 >/dev/null || dnf -q install -y python3
# Bind all public inputs before extraction, patch application or compilation.
input=$(python3 -B - <<'PY'
import hashlib, importlib.util, json, os, pathlib, re

def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def require(condition, message):
    if not condition:
        raise SystemExit(message)

root = pathlib.Path(os.environ['ROOT'])
out = pathlib.Path(os.environ['OUT'])
require(root.is_absolute() and root.is_dir() and out.is_absolute(), 'ROOT and OUT must be absolute paths')
root = root.resolve()
out = out.resolve()
require(out != root and root not in out.parents, 'OUT must be outside the read-only input bundle')
config = pathlib.PurePosixPath(os.environ['CONFIG'])
require(not config.is_absolute() and '..' not in config.parts, 'CONFIG must be relative inside ROOT')
commit = '8ab10100a4c1c944797e3fc8345589aff57777df'
archive_hash = '06acab85a9e668010fe9e664d9fc92a15d506a986faab8b9659edd4bbcaaeac2'
archive = root / f'work/sources/kernel-{commit}.tar.gz'
require(digest(archive) == archive_hash, 'Kernel archive differs from source pin')
spec = importlib.util.spec_from_file_location('candidate', root / 'outputs/persistent-install/candidate_boot.py')
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)
variant = os.environ['PROBE_VARIANT']
require(variant in candidate.BUILD_DTB_VARIANTS, 'Unsupported worker variant')
require(candidate.KERNEL_COMMIT == commit, 'Runtime source pin differs')
pins = candidate.VARIANTS[variant]
require(pins['release'] == f'7.3.0-j716s-8ab10100a4c1-{variant}', 'Runtime release pin differs')
expected_config = ('outputs/kernel-bringup-candidate/.config', 'e7ff6452a937de895bf351f8539636cbf98998493835b80113da5d5ca4ecba14') if variant in candidate.TOUCHID_VARIANTS else ('outputs/kernel-open-wifi-candidate/.config', '74a2fef4c7c36ec82cf3dd3849bc7a672f56823b48f1ef29fa1e359abdc2dd47')
require((str(config), os.environ['CONFIG_SHA256']) == expected_config, 'CONFIG binding differs from variant pin')
require((root / str(config)).resolve().is_relative_to(root), 'CONFIG escapes ROOT')
require(digest(root / str(config)) == expected_config[1], 'CONFIG differs from pin')
names = os.environ['PATCHES'].split()
require(names == list(pins['patches']), 'PATCHES differs from ordered runtime pins')
patches = []
for name in names:
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*\.patch', name), 'Unsafe patch name')
    path = root / 'patches' / name
    require(path.resolve().is_relative_to(root), 'Patch escapes ROOT')
    actual = digest(path)
    require(actual == pins['patches'][name], name + ' differs from runtime pin')
    patches.append({'name': name, 'sha256': actual})
artifacts = ('Image', 'Image.gz', 't6050-j716s.dtb', '.config', 'System.map', 'Module.symvers', 'kernel.release', 'modules.tar.gz')
out.mkdir(parents=True, exist_ok=True)
require(not any((out / name).exists() for name in (*artifacts, 'compile-receipt.json')), 'Refusing existing compiled outputs')
paths = [pathlib.Path(os.environ[name]) for name in ('SRC', 'BUILD', 'MODULE_STAGE')]
require(all(path.is_absolute() and not path.is_symlink() for path in paths), 'Cache paths must be absolute non-symlink directories')
paths = [path.resolve() for path in paths]
for index, path in enumerate(paths):
    require(len(path.parts) >= 2 and str(path) not in ('/usr', '/etc', '/var', '/root', '/home', '/tmp', '/dev', '/proc', '/sys'), 'Cache path must be a dedicated leaf directory')
    for other in [root, out, *paths[:index]]:
        require(path != other and path not in other.parents and other not in path.parents, 'Cache paths must not overlap inputs, outputs or each other')
    marker = path / '.kernel-worker-signature'
    require(not path.exists() or path.is_dir(), 'Cache path is not a directory')
    require(not path.exists() or marker.is_file() or not any(path.iterdir()), 'Refusing unowned nonempty cache directory: ' + str(path))
binding = {'commit': commit, 'variant': variant, 'kernel_release': pins['release'], 'archive_sha256': archive_hash, 'config': str(config), 'baseline_config_sha256': expected_config[1], 'ordered_patches': patches, 'worker_sha256': digest(pathlib.Path(os.environ['WORKER_PATH'])), 'host': {'arch': os.environ['BUILD_HOST_ARCH'], 'distribution': 'Fedora', 'version': '43'}, 'target': {'arch': 'arm64'}, 'toolchain': {'clang': '21.1.8', 'rust': '1.99.1'}}
signature = hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
binding['input_signature'] = signature
print(signature)
print(json.dumps(binding, separators=(',', ':')))
PY
)
signature=${input%%$'\n'*}
export WORKER_BINDING=${input#*$'\n'}
# Cached builders avoid reinstalling correct packages and pinned Rust components.
packages=(clang-21.1.8 clang-devel-21.1.8 clang-libs-21.1.8 lld-21.1.8 llvm-21.1.8 make flex bison bc openssl openssl-devel elfutils-libelf-devel python3 git perl gcc rustup findutils diffutils kmod cpio gzip zstd patch tar curl pkgconf-pkg-config ncurses-devel dtc)
if ! rpm -q "${packages[@]}" >/dev/null 2>&1; then
  dnf -q install -y "${packages[@]}"
fi
[[ ! -f ${HOME}/.cargo/env ]] || source "${HOME}/.cargo/env"
export RUSTUP_TOOLCHAIN=1.99.1
if ! rustc --version 2>/dev/null | grep -q '^rustc 1\.99\.1 '; then
  rustup-init -y -q --default-toolchain 1.99.1 --profile minimal -c rust-src -c rustfmt -c clippy
  source "${HOME}/.cargo/env"
fi
components=$(rustup component list --toolchain 1.99.1 --installed)
for component in rust-src rustfmt clippy; do
  if ! grep -Eq "^${component}(-[^ ]+)? \(installed\)$" <<< "$components"; then
    rustup component add --toolchain 1.99.1 "$component"
  fi
done
[[ $(clang -dumpversion) == 21.1.8 ]]
[[ $(ld.lld --version) == 'LLD 21.1.8'* ]]
[[ $(rustc --version) == 'rustc 1.99.1 '* ]]
# A changed signature invalidates all patched sources and generated/signing state.
reuse=true
for path in "$SRC" "$BUILD" "$MODULE_STAGE"; do
  [[ -f $path/.kernel-worker-signature && $(cat "$path/.kernel-worker-signature") == "$signature" ]] || reuse=false
done
if [[ $reuse == false ]]; then
  for path in "$SRC" "$BUILD" "$MODULE_STAGE"; do
    mkdir -p -- "$path"
    find "$path" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
    printf '%s\n' "pending-$signature" > "$path/.kernel-worker-signature"
  done
  tar -xzf "$ROOT/work/sources/kernel-8ab10100a4c1c944797e3fc8345589aff57777df.tar.gz" --strip-components=1 -C "$SRC"
  cd "$SRC"
  for name in $PATCHES; do
    patch --batch -p1 --fuzz=0 < "$ROOT/patches/$name"
  done
  python3 tools/aurora-bindgen/build.py
  for path in "$SRC" "$BUILD" "$MODULE_STAGE"; do
    printf '%s\n' "$signature" > "$path/.kernel-worker-signature"
  done
fi
cd "$SRC"
export BINDGEN="$SRC/tools/aurora-bindgen/build/bin/bindgen"
export KBUILD_BUILD_USER=codex KBUILD_BUILD_HOST=j716s-kernel-build
export KBUILD_BUILD_TIMESTAMP='Thu Nov 12 00:00:00 UTC 2026' KBUILD_BUILD_VERSION=1
# Never inherit stripping or caller architecture/toolchain overrides.
unset INSTALL_MOD_STRIP ARCH LLVM LLVM_IAS CROSS_COMPILE LOCALVERSION KCONFIG_CONFIG
common=(LLVM=1 ARCH=arm64 "O=$BUILD" "LOCALVERSION=-j716s-8ab10100a4c1-$PROBE_VARIANT")
cp "$ROOT/$CONFIG" "$BUILD/.config"
printf '%s  %s\n' "$CONFIG_SHA256" "$BUILD/.config" | sha256sum -c -
make "${common[@]}" rustavailable
make "${common[@]}" olddefconfig
cmp "$BUILD/.config" "$ROOT/$CONFIG"
nice -n 10 make -j"$JOBS" "${common[@]}" Image.gz apple/t6050-j716s.dtb modules
cmp "$BUILD/.config" "$ROOT/$CONFIG"
# Reinstall only this build's modules; retain full unstripped .ko files.
rm -rf -- "$MODULE_STAGE/lib"
make "${common[@]}" "INSTALL_MOD_PATH=$MODULE_STAGE" modules_install
[[ $(cat "$BUILD/include/config/kernel.release") == "7.3.0-j716s-8ab10100a4c1-$PROBE_VARIANT" ]]
python3 -B - <<'PY'
import os, pathlib, struct
image = pathlib.Path(os.environ['BUILD']) / 'arch/arm64/boot/Image'
with image.open('rb') as stream:
    header = stream.read(64)
if len(header) != 64 or header[56:60] != b'ARM\x64' or struct.unpack_from('<Q', header, 16)[0] == 0:
    raise SystemExit('Compiled Image is not a valid ARM64 Linux Image')
PY
cp "$BUILD/arch/arm64/boot/Image.gz" "$BUILD/arch/arm64/boot/Image" "$BUILD/arch/arm64/boot/dts/apple/t6050-j716s.dtb" "$BUILD/.config" "$BUILD/System.map" "$BUILD/Module.symvers" "$OUT/"
cp "$BUILD/include/config/kernel.release" "$OUT/kernel.release"
tar --exclude=build --exclude=source -C "$MODULE_STAGE" -czf "$OUT/modules.tar.gz" lib
python3 -B - <<'PY'
import hashlib, json, os, pathlib
out = pathlib.Path(os.environ['OUT'])
def record(path):
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'sha256': digest, 'bytes': path.stat().st_size}
receipt = json.loads(os.environ['WORKER_BINDING'])
receipt.update(schema=1, qualification='unqualified-experimental')
names = ('Image', 'Image.gz', 't6050-j716s.dtb', '.config', 'System.map', 'Module.symvers', 'kernel.release', 'modules.tar.gz')
receipt['artifacts'] = {name: record(out / name) for name in names}
receipt['patches'] = {pin['name']: pin['sha256'] for pin in receipt['ordered_patches']}
certificate = pathlib.Path(os.environ['BUILD']) / 'certs/signing_key.x509'
if 'CONFIG_MODULE_SIG_ALL=y' in (out / '.config').read_text().splitlines() and not certificate.is_file():
    raise SystemExit('Signed-module configuration has no generated signing certificate')
receipt['signing'] = {'private_key_exported': False, 'certificate': record(certificate) if certificate.is_file() else None}
receipt['build_identity'] = {'user': 'codex', 'host': 'j716s-kernel-build', 'timestamp': 'Thu Nov 12 00:00:00 UTC 2026', 'version': 1}
(out / 'compile-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
PY
chmod -R a+rX "$OUT"
