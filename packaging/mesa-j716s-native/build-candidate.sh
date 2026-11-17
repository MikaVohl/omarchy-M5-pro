#!/usr/bin/env bash
# Build-only: public pinned Mesa inputs, isolated prefix, unchanged kernel/UAPI.
set -euo pipefail
base=$(cd -- "$(dirname -- "$0")" && pwd)
cd "$base/source"
printf 'e026660582fd5cbab9fa39a4cd1e6916503802b7a657ae3c2bc831e2848d13b0  %s\n' "$base/patches/native-gl-original194.patch" | sha256sum -c -
printf '338500b05652d8c11c665784754623e9afe7a822b6b288fef084c26f8c6de3cd  %s\n' "$base/patches/vdm-context-relative.patch" | sha256sum -c -
printf '9203ec7e74a00ba67165c313dfedb116199fa3dac560f9facfcf23aeb70bb1b1  %s\n' "$base/patches/m5-uniform-dword-layout.patch" | sha256sum -c -
printf '28e1e2164a6cbd62e77d9608f89f8a5227bd2f13f7950ee759bdd32aeaf94703  %s\n' "$base/patches/native-program-diagnostics.patch" | sha256sum -c -
printf '75e496826dae573b5135b62607c1397a13d7d3a84fc5e15d0680df230b84e6a3  %s\n' "$base/source/include/drm-uapi/asahi_drm.h" | sha256sum -c -
if [[ ! -e "$base/patches-applied" ]]; then
    patch --fuzz=0 -p1 < "$base/patches/native-gl-original194.patch"
    patch --fuzz=0 -p1 < "$base/patches/vdm-context-relative.patch"
    patch --fuzz=0 -p1 < "$base/patches/m5-uniform-dword-layout.patch"
    patch --fuzz=0 -p1 < "$base/patches/native-program-diagnostics.patch"
    printf '75e496826dae573b5135b62607c1397a13d7d3a84fc5e15d0680df230b84e6a3  %s\n' "$base/source/include/drm-uapi/asahi_drm.h" | sha256sum -c -
    touch "$base/patches-applied"
fi
meson setup "$base/build" "$base/source" \
    --prefix "$base/prefix" --libdir lib --buildtype release --wrap-mode=nodownload \
    -Db_ndebug=true -Dplatforms=x11,wayland \
    -Dgallium-drivers=asahi,zink,llvmpipe,softpipe -Dvulkan-drivers=asahi -Dvulkan-layers= \
    -Degl=enabled -Dgbm=enabled -Dglx=dri -Dgles1=enabled -Dgles2=enabled \
    -Dglvnd=enabled -Dglvnd-vendor-name=j716s_native \
    -Dllvm=enabled -Dshared-llvm=enabled -Dgallium-rusticl=false \
    -Dmesa-clc=enabled -Dprecomp-compiler=enabled -Dmesa-clc-bundle-headers=enabled \
    -Dvideo-codecs= -Dinstall-mesa-clc=false -Dinstall-precomp-compiler=false \
    -Dgallium-va=disabled -Dexpat=enabled -Dzstd=enabled \
    -Dlmsensors=disabled -Dlibunwind=disabled -Dvalgrind=disabled -Ddisplay-info=disabled \
    -Dtools= -Dbuild-tests=false
ninja -C "$base/build" -j6
ninja -C "$base/build" install
# Keep Fedora's distinct LLVM SONAME private; never alias the host's LLVM23.
llvm_library=$(llvm-config --libfiles --link-shared)
[[ $llvm_library != *' '* ]] || { echo 'Expected one shared LLVM library' >&2; exit 1; }
llvm_real=$(readlink -f "$llvm_library")
llvm_soname=$(readelf -d "$llvm_real" | awk '/SONAME/ {gsub(/\[|\]/,"",$5); print $5}')
[[ -n $llvm_soname ]] || { echo 'Missing LLVM SONAME' >&2; exit 1; }
cp -L "$llvm_real" "$base/prefix/lib/$llvm_soname"
for name in libxml2.so.2 libexpat.so.1; do
    source=$(readlink -f "/usr/lib64/$name")
    [[ -f $source ]] || { echo "Missing runtime library $name" >&2; exit 1; }
    cp -L "$source" "$base/prefix/lib/$name"
done
rpm -qa --qf '%{NAME} %{VERSION}-%{RELEASE} %{ARCH}\n' | sort > "$base/compiler-packages.txt"
llvm-config --version > "$base/llvm-version.txt"
find "$base/prefix" -type f -exec sha256sum {} + > "$base/prefix-sha256.txt"
echo 'Isolated Mesa prefix built; no GPU access, installation or session switch performed.'
