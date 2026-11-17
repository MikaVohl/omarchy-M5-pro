FROM docker.io/library/fedora@sha256:f01112b9a1f1d0504ad962d9adfa401425033f8ab067e0a821904ebdde5b8ca5
RUN dnf -y install gcc gcc-c++ meson ninja-build python3-mako python3-pyyaml \
    python3-packaging python3-ply llvm-devel clang clang-devel \
    spirv-llvm-translator-devel libdrm-devel libglvnd-devel \
    wayland-devel wayland-protocols-devel libX11-devel libXext-devel \
    libxcb-devel libxshmfence-devel libXxf86vm-devel libXrandr-devel \
    expat-devel zlib-ng-compat-devel libzstd-devel elfutils-libelf-devel \
    bison flex glslang-devel spirv-tools-devel \
 && dnf clean all
RUN dnf -y install patch && dnf clean all
RUN major=$(llvm-config --version) && major=${major%%.*} \
 && resources=$(clang -print-resource-dir) \
 && test "${resources##*/}" = "$major" \
 && test -f "$resources/include/opencl-c-base.h" \
 && test -f "$resources/include/opencl-c.h" \
 && mkdir -p "$(llvm-config --libdir)/clang" \
 && ln -s "$resources" "$(llvm-config --libdir)/clang/$major"
