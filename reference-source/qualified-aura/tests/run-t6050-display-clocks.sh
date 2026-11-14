#!/bin/sh
# Run only the T6050 native display publication/provider regression; no target hardware access.
set -eu
root=$(cd "$(dirname "$0")/../../.." && pwd)
snapshot="$root/reference-source/qualified-aura"
aura="$root/work/sources/aura"
[ -d "$aura/src/libfdt" ] || { echo 'Run scripts/prepare-sources.py --fetch first.' >&2; exit 1; }
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT HUP INT TERM
cp "$snapshot/src/kboot_display_handoff.c" "$snapshot/tests/t6050_display_clocks_host.c" \
    "$snapshot"/tests/t8122_display_host/*.h "$tmp/"
${CC:-clang} -std=gnu11 -D_GNU_SOURCE -O1 -g -Wall -Wextra \
    -Wno-unused-function -Wno-unused-parameter -Wno-sign-compare \
    -ffunction-sections -fdata-sections -Wl,--gc-sections \
    -fsanitize=address,undefined -fno-omit-frame-pointer -fno-sanitize-recover=undefined \
    -I"$tmp" -I"$aura/src" "$tmp/t6050_display_clocks_host.c" \
    "$aura"/src/libfdt/fdt.c "$aura"/src/libfdt/fdt_ro.c "$aura"/src/libfdt/fdt_rw.c \
    "$aura"/src/libfdt/fdt_wip.c "$aura"/src/libfdt/fdt_sw.c \
    "$aura"/src/libfdt/fdt_empty_tree.c "$aura"/src/libfdt/fdt_strerror.c \
    "$aura"/src/libfdt/fdt_addresses.c -o "$tmp/t6050-display-clocks-host"
"$tmp/t6050-display-clocks-host"
