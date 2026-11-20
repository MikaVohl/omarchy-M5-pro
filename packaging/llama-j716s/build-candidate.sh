#!/usr/bin/env bash
# Build-only. No model execution, server, global installs or capability overrides.
set -euo pipefail
base=$(cd -- "$(dirname -- "$0")" && pwd)
(( $# >= 1 && $# <= 3 )) || { echo 'Usage: build-candidate.sh cpu|vulkan [WORKSPACE [stock|attention|borealis|borealis-gemv|borealis-q4-grouped]]' >&2; exit 2; }
route=$1
[[ $route == cpu || $route == vulkan ]] || { echo 'Expected cpu or vulkan' >&2; exit 2; }
workspace=$(realpath -m "${2:-"$base/../../work/build/llama-j716s"}")
jobs=${JOBS:-4}
[[ $jobs =~ ^[1-4]$ ]] || { echo 'JOBS must be 1..4 (bounded private build)' >&2; exit 2; }
# Preparation stays local unless the separate --fetch command was requested.
python3 "$base/prepare-candidate.py" --route "$route" --toolchain --variant "${3:-stock}" "$workspace"
exec 9>"$workspace/.build.lock"
flock --nonblock 9 || { echo 'Candidate workspace is already being built' >&2; exit 1; }
build="$workspace/build-$route"
[[ ! -e $build && ! -L $build ]] || { echo "Refusing to rebuild/overwrite $build; choose a fresh private workspace" >&2; exit 1; }
mkdir "$build"
mkdir -p "$workspace/build-home"
# Ignore ambient compiler/SDK/backend/cache settings; all paths stay private.
clean_env=(env -i "HOME=$workspace/build-home" PATH=/usr/bin:/bin LC_ALL=C TZ=UTC
           SOURCE_DATE_EPOCH=1794565219 "CMAKE_BUILD_PARALLEL_LEVEL=$jobs" "MAKEFLAGS=-j$jobs")
common=(-G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=/usr/bin/gcc
        -DCMAKE_CXX_COMPILER=/usr/bin/g++ -DCMAKE_MAKE_PROGRAM=/usr/bin/ninja
        "-DCMAKE_C_FLAGS=-ffile-prefix-map=$workspace=/llama-j716s"
        "-DCMAKE_CXX_FLAGS=-ffile-prefix-map=$workspace=/llama-j716s"
        -DBUILD_SHARED_LIBS=OFF -DGGML_BACKEND_DL=OFF -DGGML_NATIVE=OFF
        -DGGML_CPU_ARM_ARCH=armv8.6-a+dotprod+fp16+i8mm -DGGML_CPU=ON
        -DGGML_CPU_KLEIDIAI=OFF -DGGML_CCACHE=OFF -DGGML_LTO=OFF
        -DGGML_BLAS=OFF -DGGML_ACCELERATE=OFF -DGGML_METAL=OFF
        -DGGML_CUDA=OFF -DGGML_HIP=OFF -DGGML_SYCL=OFF -DGGML_RPC=OFF
        -DGGML_OPENMP=ON -DGGML_OPENMP_FETCH=OFF
        -DLLAMA_BUILD_IS_DEV=OFF -DLLAMA_BUILD_NUMBER=0
        -DLLAMA_BUILD_COMMIT=2d8a32502909ca065c41c4cf5f2fb1c427c047fc
        -DLLAMA_BUILD_COMMON=ON -DLLAMA_BUILD_TOOLS=ON
        -DLLAMA_BUILD_TESTS=OFF -DGGML_BUILD_TESTS=OFF
        -DLLAMA_BUILD_EXAMPLES=OFF -DGGML_BUILD_EXAMPLES=OFF
        -DLLAMA_BUILD_SERVER=OFF -DLLAMA_BUILD_APP=OFF -DLLAMA_BUILD_UI=OFF
        -DLLAMA_CONNECT=OFF -DLLAMA_LLGUIDANCE=OFF
        -DLLAMA_OPENSSL=OFF -DLLAMA_SUBPROCESS=OFF
        -DLLAMA_TOOLS_INSTALL=OFF -DLLAMA_TESTS_INSTALL=OFF
        -DFETCHCONTENT_FULLY_DISCONNECTED=ON -DFETCHCONTENT_UPDATES_DISCONNECTED=ON)
backend=(-DGGML_VULKAN=OFF)
if [[ $route == vulkan ]]; then
    # Header-only CMake package, installed only into the candidate workspace.
    sdk="$workspace/private-sdk"
    [[ ! -e $sdk && ! -L $sdk ]] || { echo 'Refusing to overwrite an existing private SDK' >&2; exit 1; }
    "${clean_env[@]}" cmake -S "$workspace/spirv-headers" -B "$workspace/build-spirv-headers" \
        -G Ninja -DCMAKE_C_COMPILER=/usr/bin/gcc -DCMAKE_CXX_COMPILER=/usr/bin/g++ \
        -DCMAKE_MAKE_PROGRAM=/usr/bin/ninja "-DCMAKE_INSTALL_PREFIX=$sdk" \
        -DSPIRV_HEADERS_ENABLE_TESTS=OFF -DSPIRV_HEADERS_ENABLE_INSTALL=ON
    "${clean_env[@]}" cmake --install "$workspace/build-spirv-headers"
    # Upstream finds SPIRV-HeadersConfig but does not link its include target.
    # Make the pinned private include tree visible only to this build.
    clean_env+=("CPLUS_INCLUDE_PATH=$sdk/include")
    backend=(-DGGML_VULKAN=ON "-DVulkan_INCLUDE_DIR=$workspace/vulkan-headers/include"
             -DVulkan_LIBRARY=/usr/lib/libvulkan.so -DVulkan_GLSLC_EXECUTABLE=/usr/bin/glslc
             "-DSPIRV-Headers_DIR=$sdk/share/cmake/SPIRV-Headers")
fi
"${clean_env[@]}" cmake -S "$workspace/source" -B "$build" "${common[@]}" "${backend[@]}"
# Build only text completion and bench, not all configured optional tools.
"${clean_env[@]}" cmake --build "$build" --parallel "$jobs" --target llama-completion llama-bench
python3 - "$base" "$workspace" "$route" "$jobs" <<'PY'
import hashlib
import json
from pathlib import Path
import subprocess
import sys
base, work = map(Path, sys.argv[1:3])
route, jobs = sys.argv[3:]
def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()
manifest = json.loads((base / 'inputs.json').read_text())
receipt = {
    'schema': 1, 'status': 'built-not-runtime-qualified', 'route': route,
    'llama_commit': manifest['llama']['commit'], 'jobs': int(jobs),
    'inputs_sha256': digest(base / 'inputs.json'),
    'recipe_sha256': {name: digest(base / name) for name in ('build-candidate.sh', 'prepare-candidate.py')},
    'source_receipt_sha256': digest(work / 'source-receipt.json'),
    'source_patches': json.loads((work / 'source-receipt.json').read_text()).get('patches', {}),
    'outputs_sha256': {name: digest(work / f'build-{route}/bin' / name) for name in manifest['llama']['targets']},
    'packages': subprocess.check_output(['/usr/bin/pacman', '-Q'], text=True).splitlines(),
    'cmake_cache_sha256': digest(work / f'build-{route}/CMakeCache.txt'),
    'compiler_commands_sha256': digest(work / f'build-{route}/compile_commands.json'),
}
with (work / f'build-{route}-receipt.json').open('x') as output:
    json.dump(receipt, output, indent=2, sort_keys=True)
    output.write('\n')
PY
printf 'Private %s candidate built at %s/bin; no inference or global installation executed.\n' "$route" "$build"
