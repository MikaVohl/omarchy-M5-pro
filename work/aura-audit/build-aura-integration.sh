#!/usr/bin/env bash
# Pinned source and qualified handoff patches; build trees remain inside the guest.
set -euo pipefail
source /root/.cargo/env
[[ $(uname -m) == aarch64 && $(rpm -E '%{fedora}') == 43 ]]
[[ $(clang -dumpversion) == 21.1.8 ]]
[[ $(rustc --version) == 'rustc 1.99.1 '* ]]
base=54678354b70d64506408d9b157f85ac6065d194b
mkdir -p /src
cp -a /exchange/work/sources/aura /src/aura
cd /src/aura
git config --global --add safe.directory /src/aura
[[ $(git rev-parse HEAD) == "$base" ]]
patches=(work/aura-audit/j716s-26C5035r-integration.patch patches/aura-j716s-fixed-clocks.patch patches/aura-j716s-kis-debug.patch)
for patch in "${patches[@]}"; do git apply "/exchange/$patch"; done
cmp src/kboot_display_handoff.c /exchange/reference-source/qualified-aura/src/kboot_display_handoff.c
cmp src/startup.c /exchange/reference-source/qualified-aura/src/startup.c
out=/exchange/outputs/boot-candidate/loader
mkdir -p "$out"
# New build tag: preserve the kis05 source behavior without asserting historical byte identity.
export AURA_VERSION_TAG=1.0.0b12-g54678354-j716s-qualified-rebuild
for stage in stage1 stage2; do
  make clean
  flags=(USE_CLANG=1 ARCH=aarch64-linux-gnu BUILDSTD=1 RELEASE=1 CHAINLOADING=1 BUILTIN_LOGO=aurora)
  if [[ "$stage" == stage2 ]]; then flags+=(ESP_STAGE2=1 EXTRA_CFLAGS=-DJ716S_KIS_DIAGNOSTIC); fi
  make -j"${JOBS:-4}" "${flags[@]}"
  mkdir -p "$out/$stage"
  cp build/aura.bin build/aura.macho build/aura-boot.macho build/aura-raw.elf build/build_cfg.h build/build_tag.h "$out/$stage/"
done
python3 - "$out" "$base" "${patches[@]}" <<'PY'
import hashlib, json, pathlib, sys
out = pathlib.Path(sys.argv[1])
def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()
stages = {}
for stage in ('stage1', 'stage2'):
    names = ('aura.bin', 'aura.macho', 'aura-boot.macho', 'aura-raw.elf', 'build_cfg.h', 'build_tag.h')
    stages[stage] = {name: {'sha256': digest(out / stage / name),
                           'bytes': (out / stage / name).stat().st_size} for name in names}
    (out / stage / 'SHA256SUMS').write_text(''.join(f"{value['sha256']}  {name}\n" for name, value in stages[stage].items()))
patches = [{'path': path, 'sha256': digest(pathlib.Path('/exchange') / path)} for path in sys.argv[3:]]
(out / 'build-manifest.json').write_text(json.dumps({'schema': 1, 'base_commit': sys.argv[2],
    'patches': patches, 'stages': stages}, indent=2) + '\n')
PY
