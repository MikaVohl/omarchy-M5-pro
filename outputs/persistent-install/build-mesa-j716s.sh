#!/usr/bin/env bash
# Prepare public sources outside the image; build only in the marked image chroot.
set -euo pipefail
commit=a8e86a80cdc4695d00e7e63b8ebea263e557431b
archive_sha=fa866896387c27e57263e4f1c7764d3daa3b4d2de7a6d1f04da4ecb25f73b421
prefix=/opt/mesa-j716s
mode= workspace=
while (($#)); do
  case "$1" in
    --prepare) mode=prepare; workspace=${2:?workspace required}; shift 2;;
    --image-build) mode=image; shift;;
    --workspace) workspace=${2:?workspace required}; shift 2;;
    *) echo "usage: $0 --prepare WORKSPACE | --image-build --workspace WORKSPACE" >&2; exit 2;;
  esac
done
[[ -n $mode && -n $workspace && $workspace = /* && $workspace != / ]] || exit 2
if [[ $mode = prepare ]]; then
  assets=$(cd -- "$(dirname -- "$0")/graphics" && pwd)
  python3 - "$workspace" "$assets" "$commit" "$archive_sha" <<'PY'
import hashlib, json, os, pathlib, shutil, sys, urllib.request
workspace, assets = map(pathlib.Path, sys.argv[1:3])
commit, expected = sys.argv[3:5]
workspace.mkdir(parents=True, exist_ok=True)
archive = workspace / 'mesa-source.tar.gz'
if not archive.exists():
    temp = workspace / 'mesa-source.tar.gz.partial'
    with urllib.request.urlopen('https://codeload.github.com/aurora-silicon/mesa/tar.gz/' + commit) as response, temp.open('wb') as output:
        shutil.copyfileobj(response, output)
    temp.replace(archive)
with archive.open('rb') as f:
    h = hashlib.sha256()
    for block in iter(lambda: f.read(1024 * 1024), b''):
        h.update(block)
    digest = h.hexdigest()
if digest != expected:
    raise SystemExit('Mesa source archive SHA256 mismatch: ' + digest)
integration = workspace / 'integration'
integration.mkdir(exist_ok=True)
for name in ('graphics-manifest.json', 'j716s-gpu-preflight.py', 'j716s-session-env.py'):
    shutil.copyfile(assets / name, integration / name)
print(json.dumps({'workspace': str(workspace), 'mesa_commit': commit, 'archive_sha256': digest}))
PY
  exit
fi
[[ $EUID = 0 && ${J716S_IMAGE_BUILD:-} = 1 && $(uname -m) = aarch64 ]] || {
  echo 'Refusing Mesa build outside explicit ARM64 image chroot' >&2; exit 1;
}
[[ -f /etc/j716s/image-build && $(cat /etc/j716s/image-build) = j716s-arm64-image-v1 ]] || exit 1
source /etc/os-release
case "$ID" in
  archarm|archlinuxarm|arch) ;;
  omarchy) [[ " ${ID_LIKE:-} " = *" arch "* ]] || { echo 'Omarchy image is not Arch-derived' >&2; exit 1; } ;;
  *) echo 'ArchLinuxARM or provisioned Omarchy image required' >&2; exit 1 ;;
esac
mountpoint -q "$workspace" || { echo 'External Mesa workspace bind mount required' >&2; exit 1; }
[[ ! -e $prefix ]] || { echo 'Refusing to overlay an existing Mesa prefix' >&2; exit 1; }
python3 - "$workspace" "$commit" "$archive_sha" <<'PY'
import hashlib, pathlib, sys, tarfile
workspace = pathlib.Path(sys.argv[1]); commit, expected = sys.argv[2:4]
archive = workspace / 'mesa-source.tar.gz'
with archive.open('rb') as f:
    if hashlib.file_digest(f, 'sha256').hexdigest() != expected:
        raise SystemExit('Mesa source archive SHA256 mismatch')
source = workspace / ('mesa-' + commit)
if source.exists():
    raise SystemExit('Use a fresh external workspace: existing extracted source would be unverified')
with tarfile.open(archive, 'r:gz') as tf:
    tf.extractall(workspace, filter='data')
header = source / 'include/drm-uapi/asahi_drm.h'
if hashlib.sha256(header.read_bytes()).hexdigest() != '75e496826dae573b5135b62607c1397a13d7d3a84fc5e15d0680df230b84e6a3':
    raise SystemExit('Pinned Mesa UAPI header mismatch')
PY
src=$workspace/mesa-$commit
build=$workspace/build
[[ ! -e $build ]] || { echo 'Fresh build workspace required' >&2; exit 1; }
options=(
  --prefix "$prefix" --libdir lib --buildtype release --wrap-mode=nodownload
  -Db_ndebug=true -Dplatforms=x11,wayland
  -Dgallium-drivers=asahi,zink,llvmpipe,softpipe -Dvulkan-drivers=asahi
  -Dvulkan-layers= -Degl=enabled -Dgbm=enabled -Dglx=dri
  -Dgles1=enabled -Dgles2=enabled -Dglvnd=enabled -Dglvnd-vendor-name=j716s
  -Dllvm=enabled -Dshared-llvm=enabled -Dgallium-rusticl=false
  -Dmesa-clc=auto -Dprecomp-compiler=auto -Dvideo-codecs=
  -Dinstall-mesa-clc=false -Dinstall-precomp-compiler=false
  -Dgallium-va=disabled -Dexpat=enabled -Dzstd=enabled -Dlmsensors=disabled
  -Dlibunwind=disabled -Dvalgrind=disabled -Ddisplay-info=disabled
  -Dtools= -Dbuild-tests=false
)
meson setup "$build" "$src" "${options[@]}"
ninja -C "$build" -j "${J716S_MESA_JOBS:-$(nproc)}"
ninja -C "$build" install
install -d "$prefix/share/j716s" "$prefix/share/glvnd/egl_vendor.d" /usr/local/bin /usr/local/libexec
install -m644 "$workspace/integration/graphics-manifest.json" "$prefix/share/j716s/graphics-manifest.json"
install -m755 "$workspace/integration/j716s-gpu-preflight.py" /usr/local/bin/j716s-gpu-preflight
install -m755 "$workspace/integration/j716s-session-env.py" /usr/local/libexec/j716s-session-env
printf '%s\n' "$commit" > "$prefix/MESA_COMMIT"
python3 - "$prefix" "$workspace" "$commit" "$archive_sha" "${options[@]}" <<'PY'
import hashlib, json, os, pathlib, re, subprocess, sys
prefix, workspace = map(pathlib.Path, sys.argv[1:3]); commit, archive_sha = sys.argv[3:5]
metadata = prefix / 'share/j716s'
egl = prefix / 'lib/libEGL_j716s.so.0'
if not egl.exists() or not (prefix / 'lib/libGLX_j716s.so.0').exists() or not (prefix / 'lib/libgbm.so.1').exists():
    raise SystemExit('Missing private EGL/GLX/GBM output')
(metadata.parent / 'glvnd/egl_vendor.d/50_j716s.json').write_text(json.dumps({
    'file_format_version': '1.0.0', 'ICD': {'library_path': str(egl)}}, indent=2) + '\n')
icds = sorted((prefix / 'share/vulkan/icd.d').glob('asahi_icd*.json'))
if len(icds) != 1:
    raise SystemExit('Expected one native Asahi Vulkan ICD')
icd = json.loads(icds[0].read_text())
library = icd['ICD']['library_path']
if not os.path.isabs(library):
    candidates = [prefix / 'lib' / pathlib.Path(library).name, (icds[0].parent / library).resolve()]
    resolved = next((p for p in candidates if p.exists() and p.is_relative_to(prefix)), None)
    if resolved is None:
        raise SystemExit('Native ICD library missing from private prefix')
    icd['ICD']['library_path'] = str(resolved)
elif not pathlib.Path(library).is_relative_to(prefix) or not pathlib.Path(library).exists():
    raise SystemExit('ICD library escaped private prefix')
icds[0].write_text(json.dumps(icd, indent=2) + '\n')
packages = subprocess.check_output(['pacman', '-Q'], text=True).splitlines()
files = []; needed = {}
for path in sorted(prefix.rglob('*')):
    if 'shim' in path.name.lower():
        raise SystemExit('Production prefix unexpectedly contains a DRM shim')
    if path.is_symlink():
        files.append({'path': str(path.relative_to(prefix)), 'symlink': os.readlink(path)})
    elif path.is_file():
        with path.open('rb') as f:
            magic = f.read(4)
            f.seek(0); digest = hashlib.file_digest(f, 'sha256').hexdigest()
        files.append({'path': str(path.relative_to(prefix)), 'size': path.stat().st_size, 'sha256': digest})
        if magic == b'\x7fELF':
            dynamic = subprocess.check_output(['readelf', '-d', str(path)], text=True)
            needed[str(path.relative_to(prefix))] = re.findall(r'\(NEEDED\).*\[([^\]]+)\]', dynamic)
inventory = {'schema': 'j716s-mesa-prefix-inventory-v1', 'prefix': str(prefix), 'files': files,
             'scope': 'all installed prefix content except this inventory and build receipt, recorded after native install'}
encoded = (json.dumps(inventory, indent=2, sort_keys=True) + '\n').encode()
(metadata / 'prefix-inventory.json').write_bytes(encoded)
receipt = {'schema': 'j716s-mesa-build-v1', 'mesa_commit': commit, 'source_archive_sha256': archive_sha,
    'prefix': str(prefix), 'architecture': os.uname().machine, 'meson_options': sys.argv[5:],
    'packages_at_build': packages, 'needed_libraries': needed,
    'inventory_sha256': hashlib.sha256(encoded).hexdigest(),
    'prefix_content_sha256': hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
    'vulkan_icd': str(icds[0]), 'graphics_manifest_sha256': hashlib.sha256((metadata / 'graphics-manifest.json').read_bytes()).hexdigest(),
    'abi': json.loads((metadata / 'graphics-manifest.json').read_text())['abi'],
    'build_environment': 'explicit ArchLinuxARM image chroot; source and objects on external bind workspace',
    'gpu_runtime_exercised': False, 'dcp_physical_qualified': False, 'source_patches': []}
(metadata / 'build-receipt.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
print(json.dumps({'mesa_commit': commit, 'receipt': str(metadata / 'build-receipt.json'),
                  'prefix_content_sha256': receipt['prefix_content_sha256']}))
PY
