#!/usr/bin/env python3
"""Host-only ANE identity/reservation consumer regression.

Run after integration with --kernel FRESH_PATCHED_SOURCE. Compiles the actual
cold-handoff consumer, platform table, reservation reader and admission helpers;
only OF/resource/hash boundaries are synthetic. Does not inspect source strings
as evidence of behavior, open hardware, start an engine or submit DMA. The DT
property addition is deliberately not a replacement for loader cold-boot evidence.
"""
import argparse
import importlib.util
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

SPEC = importlib.util.spec_from_file_location(
    "ane_geometry_regression", Path(__file__).with_name("test-ane-27_3.py"))
production = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(production)


def harness(kernel):
    directory = kernel / "drivers/accel/ane"
    platform_h = (directory / "ane_fw_platform.h").read_text()
    platform = (directory / "ane_fw_platform.c").read_text()
    cold_h = (directory / "ane_fw_cold.h").read_text()
    cold = (directory / "ane_fw_cold.c").read_text()
    platform_h = re.sub(r"^#include[^\n]*\n", "", platform_h, flags=re.M)
    declarations = "\n".join(production.block(source, name) for source, names in (
        (platform, ("ane_h17_reg_names", "ane_h17_reg_sizes", "ane_t6050_text_pin_27_0",
                    "ane_t6050_text_pin_27_3", "ane_t6050_builds", "ane_t6050_platform")),
        (cold_h, ("ane_fw_cold_state", "ane_fw_handoff"))) for name in names)
    helpers = "\n".join(production.function(cold_h, name) for name in
                        ("ane_fw_cold_pmgr_valid", "ane_fw_cold_handoff_loader_ok",
                         "ane_fw_cold_handoff_valid"))
    defines = "\n".join(line for line in cold_h.splitlines()
                        if line.startswith("#define ANE_FW_"))
    return (PRELUDE + defines + "\n" + platform_h + declarations + helpers +
            BOUNDARIES + production.function(platform, "ane_fw_preload_range") +
            production.function(cold, "ane_fw_handoff_present") + CASES)


PRELUDE = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
#define U64_MAX UINT64_MAX
#define SZ_16K 0x4000ULL
#define SZ_1M 0x100000ULL
#define SZ_512M 0x20000000ULL
#define BIT(n) (1U << (n))
#define ARRAY_SIZE(x) (sizeof(x) / sizeof((x)[0]))
#define IS_ALIGNED(n,a) (!((n) & ((a) - 1)))
#define check_add_overflow(a,b,c) __builtin_add_overflow((a),(b),(c))
#define __free(x)
#define MEMREMAP_WB 1
#define REGION_DISJOINT 0
#define IORESOURCE_SYSTEM_RAM 1
#define IORES_DESC_NONE 0
struct device;
static bool ane_fw_observe_only;
static void discard_log(const char *fmt, ...) { (void)fmt; }
#define dev_info(dev,...) ((void)(dev), discard_log(__VA_ARGS__))
'''


BOUNDARIES = r'''
struct device_node {
    struct device_node *parent, *regions[2];
    bool available, no_map, reusable, ram, preloaded;
    u64 physical, size, dva[2];
    int region_count, name_count, iova_count, type_error;
    const char *names[2], *marker;
    u32 ane_type;
};
struct device { struct device_node *of_node; };
struct platform_device { struct device dev; };
struct resource { u64 start, end; };
static struct device_node chosen, reserved, node, text_mem, data_mem;
static u8 measured[32], text_byte;
static unsigned int mappings, hashes;
static size_t hashed_bytes;
static bool map_failure;
static struct device_node *of_find_node_by_path(const char *path) {
    if (!strcmp(path,"/chosen")) return &chosen;
    assert(!strcmp(path,"/reserved-memory")); return &reserved;
}
static struct device_node *of_parse_phandle(struct device_node *np, const char *key, unsigned int i) {
    assert(!strcmp(key,"memory-region") && i < 2); return np->regions[i];
}
static bool of_device_is_available(struct device_node *np) { return np->available; }
static bool of_property_read_bool(struct device_node *np, const char *key) {
    if (!strcmp(key,"no-map")) return np->no_map;
    if (!strcmp(key,"reusable")) return np->reusable;
    assert(!strcmp(key,"apple,firmware-preloaded")); return np->preloaded;
}
static int of_address_to_resource(struct device_node *np, int i, struct resource *r) {
    assert(!i); if (!np->size || np->size - 1 > U64_MAX - np->physical) return -EINVAL;
    r->start = np->physical; r->end = np->physical + np->size - 1; return 0;
}
static u64 resource_size(const struct resource *r) { return r->end - r->start + 1; }
static int region_intersects(u64 start, u64 size, int flags, int desc) {
    (void)flags; (void)desc;
    struct device_node *np = start == text_mem.physical ? &text_mem : &data_mem;
    assert(start == np->physical && size == np->size); return np->ram ? 1 : REGION_DISJOINT;
}
static int of_property_read_string(struct device_node *np, const char *key, const char **out) {
    assert(!strcmp(key,"apple,ane-cold-boot")); *out = np->marker; return *out ? 0 : -EINVAL;
}
static int of_property_read_u32(struct device_node *np, const char *key, u32 *out) {
    assert(!strcmp(key,"apple,ane-type"));
    if (np->type_error) return np->type_error;
    *out = np->ane_type; return 0;
}
static int of_property_count_u32_elems(struct device_node *np, const char *key) {
    assert(!strcmp(key,"memory-region")); return np->region_count;
}
static int of_property_count_strings(struct device_node *np, const char *key) {
    assert(!strcmp(key,"memory-region-names")); return np->name_count;
}
static int of_property_count_u64_elems(struct device_node *np, const char *key) {
    assert(!strcmp(key,"apple,firmware-iovas")); return np->iova_count;
}
static int of_property_read_u64_array(struct device_node *np, const char *key, u64 *out, int count) {
    assert(!strcmp(key,"apple,firmware-iovas") && count == 2);
    memcpy(out,np->dva,sizeof(np->dva)); return np->iova_count == 2 ? 0 : -EINVAL;
}
static int of_property_read_string_index(struct device_node *np, const char *key,
                                         unsigned int i, const char **out) {
    assert(!strcmp(key,"memory-region-names") && i < 2);
    *out = np->names[i]; return *out ? 0 : -EINVAL;
}
static void *memremap(u64 physical, u64 size, int mode) {
    assert(mode == MEMREMAP_WB && physical == text_mem.physical && size == 0xec000);
    mappings++; return map_failure ? NULL : &text_byte;
}
static void memunmap(void *p) { assert(p == &text_byte); }
static void sha256(const void *p, size_t size, u8 *out) {
    assert(p == &text_byte); hashes++; hashed_bytes = size; memcpy(out,measured,32);
}
'''

CASES = r'''
static void fixture(void) {
    memset(&node,0,sizeof(node)); memset(&chosen,0,sizeof(chosen));
    text_mem = (struct device_node){ .parent = &reserved, .available = true, .no_map = true,
                                    .physical = 0x100000000ULL, .size = 0xec000 };
    data_mem = text_mem; data_mem.physical = 0x200000000ULL; data_mem.size = 0x280000;
    node.preloaded = true; node.ane_type = 0x220;
    node.region_count = node.name_count = node.iova_count = 2;
    node.names[0] = "firmware-text"; node.names[1] = "firmware-data";
    node.regions[0] = &text_mem; node.regions[1] = &data_mem;
    node.dva[0] = 0x10000000000ULL; node.dva[1] = 0x100000ec000ULL;
    chosen.marker = ANE_FW_COLD_BOOT_EVIDENCE;
    memcpy(measured,ane_t6050_text_pin_27_3,32);
    mappings = hashes = 0; hashed_bytes = 0; map_failure = false;
    ane_fw_observe_only = true;
}
static bool handoff(struct ane_fw_handoff *h) {
    struct platform_device p = { .dev.of_node = &node };
    return ane_fw_handoff_present(&p,&ane_t6050_platform,h);
}
static void identity_tests(void) {
    struct ane_fw_handoff h;
    fixture(); assert(!handoff(&h));
    assert(h.preloaded && h.regions_ok && h.text_ok && hashes == 1 && mappings == 1);
    assert(hashed_bytes == 0xec000 && h.build == &ane_t6050_builds[1]);
    const int errors[] = { -EINVAL, -ENODATA, -EOVERFLOW };
    for (unsigned int i = 0; i < ARRAY_SIZE(errors); i++) {
        fixture(); node.type_error = errors[i];
        assert(!handoff(&h) && !h.preloaded && h.regions_ok && !mappings && !hashes);
    }
    const u32 wrong[] = { 0, 0x110, 0x200, 0x221, 0x20020000, UINT32_MAX };
    for (unsigned int i = 0; i < ARRAY_SIZE(wrong); i++) {
        fixture(); node.ane_type = wrong[i];
        assert(!handoff(&h) && !h.preloaded && h.regions_ok && !mappings && !hashes);
    }
    fixture(); node.preloaded = false;
    assert(!handoff(&h) && !h.preloaded && !mappings && !hashes);
}
static void reservation_tests(void) {
    struct ane_fw_handoff h;
    for (unsigned int n = 0; n < 20; n++) {
        fixture();
        switch (n) {
        case 0: node.region_count = 1; break;
        case 1: node.region_count = 3; break;
        case 2: node.name_count = 1; break;
        case 3: node.iova_count = 3; break;
        case 4: node.names[0] = "firmware-data"; break;
        case 5: node.names[1] = "foreign-data"; break;
        case 6: text_mem.available = false; break;
        case 7: data_mem.available = false; break;
        case 8: text_mem.no_map = false; break;
        case 9: data_mem.no_map = false; break;
        case 10: text_mem.reusable = true; break;
        case 11: data_mem.reusable = true; break;
        case 12: text_mem.ram = true; break;
        case 13: data_mem.ram = true; break;
        case 14: text_mem.parent = &chosen; break;
        case 15: node.regions[1] = NULL; break;
        case 16: text_mem.size = 0; break;
        case 17: text_mem.physical++; break;
        case 18: data_mem.physical = text_mem.physical + SZ_16K; break;
        case 19: node.dva[1] = 0x100000c0000ULL; break;
        }
        assert(!handoff(&h) && !mappings && !hashes);
    }
}
static void ownership_tests(void) {
    struct ane_fw_handoff h;
    fixture(); chosen.marker = NULL; assert(!handoff(&h) && hashes == 1);
    assert(!ane_fw_cold_handoff_loader_ok(&h,&ane_t6050_platform));
    fixture(); chosen.marker = "not-a-cold-boot"; assert(!handoff(&h) && hashes == 1);
    assert(!ane_fw_cold_handoff_loader_ok(&h,&ane_t6050_platform));
    fixture(); measured[31] ^= 1; assert(!handoff(&h) && hashes == 1 && !h.text_ok);
    assert(!ane_fw_cold_handoff_loader_ok(&h,&ane_t6050_platform));
    fixture(); map_failure = true; assert(!handoff(&h) && mappings == 1 && !hashes);
    fixture(); assert(!handoff(&h) && ane_fw_observe_only);
    assert(ane_fw_cold_handoff_loader_ok(&h,&ane_t6050_platform));
    struct ane_fw_cold_state pmgr = { .subblock = 0x300 };
    h.pmgr = &pmgr; assert(ane_fw_cold_handoff_valid(&h,&ane_t6050_platform));
    h.crash_kernel = true; assert(!ane_fw_cold_handoff_valid(&h,&ane_t6050_platform));
    h.crash_kernel = false; pmgr.cpu = 1;
    assert(!ane_fw_cold_handoff_valid(&h,&ane_t6050_platform));
    pmgr.cpu = 0; pmgr.subblock = 0;
    assert(!ane_fw_cold_handoff_valid(&h,&ane_t6050_platform));
    pmgr.subblock = 0x300; pmgr.sys = 1;
    assert(ane_fw_cold_handoff_valid(&h,&ane_t6050_platform));
    assert(!ane_fw_cold_pmgr_valid(&pmgr,false));
    h.pmgr = NULL; assert(!ane_fw_cold_handoff_valid(&h,&ane_t6050_platform));
}
int main(void) {
    identity_tests(); reservation_tests(); ownership_tests();
    puts("PASS: actual ANE type consumer rejects missing/wrong metadata before TEXT read; reservation, identity, loader, PMGR, crash and observe-only gates preserved");
    return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="ane-type-metadata-host-") as temporary:
        directory = Path(temporary)
        source, executable = directory / "check.c", directory / "check"
        source.write_text(harness(args.kernel))
        subprocess.run([*shlex.split(os.environ.get("CC", "cc")), "-std=gnu11", "-O2",
                        "-Wall", "-Wextra", "-Werror", "-o", str(executable), str(source)],
                       check=True, timeout=60)
        subprocess.run([str(executable)], check=True, timeout=10, preexec_fn=production.no_core)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
