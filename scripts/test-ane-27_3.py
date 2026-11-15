#!/usr/bin/env python3
"""Host-only regression tests for the patched ANE 27.3 admission/IPC paths.

Compiles the actual platform, reservation, handoff, channel parser, transport
and boot bodies from --kernel. DT/resource/MMIO/hash operations are synthetic
boundaries, not an alternative implementation. No firmware, device node,
engine, boot selection or physical memory is opened. Run only after applying
kernel-j716s-media-handoff-diagnostics.patch then kernel-j716s-ane-27_3.patch.
"""
import argparse
import os
from pathlib import Path
import re
import resource
import shlex
import subprocess
import tempfile


def block(source, name):
    match = re.search(r"^.*\b" + re.escape(name) + r"(?:\[\])?\s*(?:=\s*)?\{", source, re.M)
    if not match:
        raise ValueError("missing production declaration: " + name)
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    if source[end] != ";":
        raise ValueError("unterminated production declaration: " + name)
    return source[match.start():end + 1]


def function(source, name):
    match = re.search(r"\b" + re.escape(name) + r"\([^;]*?\)\n\{", source)
    if not match:
        raise ValueError("missing production function: " + name)
    start = source.rfind("\n", 0, match.start()) + 1
    previous = source.rfind("\n", 0, start - 1) + 1
    if source[previous:start].startswith("static inline"):
        start = previous
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def replaced(source, old, new):
    if source.count(old) != 1:
        raise ValueError("negative control no longer matches exactly: " + old)
    return source.replace(old, new)


def harness(kernel, mutation=None):
    directory = kernel / "drivers/accel/ane"
    platform_h = (directory / "ane_fw_platform.h").read_text()
    platform = (directory / "ane_fw_platform.c").read_text()
    protocol = (directory / "ane_fw_protocol.h").read_text()
    transport = (directory / "ane_fw_transport.h").read_text()
    cold_h = (directory / "ane_fw_cold.h").read_text()
    cold = (directory / "ane_fw_cold.c").read_text()
    bodies = {name: function(cold, name) for name in
              ("ane_fw_handoff_present", "ane_fw_boot", "ane_fw_preload_metadata")}
    if mutation == "mapped-window":
        bodies["ane_fw_boot"] = replaced(bodies["ane_fw_boot"],
                                         "ipc.size = ipc_bytes;", "ipc.size = b->ipc.size;")
    elif mutation == "unbounded-observation":
        bodies["ane_fw_handoff_present"] = replaced(bodies["ane_fw_handoff_present"],
                                                     "if (pin) {", "if (h.regions_ok) {")
    elif mutation == "no-text-pin":
        bodies["ane_fw_handoff_present"] = replaced(bodies["ane_fw_handoff_present"],
            "h.text_ok = !memcmp(h.text_digest, pin, sizeof(h.text_digest));", "h.text_ok = true;")
    elif mutation == "observation-start":
        bodies["ane_fw_handoff_present"] = replaced(bodies["ane_fw_handoff_present"],
                                                     " && !ane_fw_observe_only", "")
    elif mutation == "wrong-init-size":
        bodies["ane_fw_boot"] = replaced(bodies["ane_fw_boot"],
            "put_unaligned_le64(ipc_bytes, init + 0x50);", "put_unaligned_le64(0xc441, init + 0x50);")
    defines = "\n".join(line for source in (protocol, transport, cold_h, cold)
                         for line in source.splitlines() if line.startswith("#define ANE_FW_"))
    definitions = "\n".join(block(source, name) for source, name in (
        (platform, "ane_h17_reg_names"), (platform, "ane_h17_reg_sizes"),
        (platform, "ane_t6050_text_pin_27_0"), (platform, "ane_t6050_text_pin_27_3"),
        (platform, "ane_t6050_builds"), (platform, "ane_t6050_platform"),
        (protocol, "ane_fw_channel"), (transport, "ane_fw_surface"),
        (transport, "ane_fw_transport_ops"), (transport, "ane_fw_transaction"),
        (transport, "ane_fw_transport"), (cold_h, "ane_fw_cold_state"), (cold_h, "ane_fw_handoff")))
    # The platform header is taken whole, apart from unavailable kernel includes.
    platform_h = re.sub(r"^#include[^\n]*\n", "", platform_h, flags=re.M)
    pure = "\n".join(function(source, name) for source, names in (
        (protocol, ("ane_fw_contains", "ane_fw_channel_name", "ane_fw_channels_parse")),
        (cold_h, ("ane_fw_heap_tail", "ane_fw_cold_pmgr_valid", "ane_fw_cold_handoff_loader_ok",
                  "ane_fw_cold_handoff_valid")),
        (transport, ("ane_fw_surface_valid", "ane_fw_surfaces_overlap", "__ane_fw_transport_fail",
                     "ane_fw_control_channel", "ane_fw_transport_init", "ane_fw_transport_check",
                     "ane_fw_transport_arm"))) for name in names)
    return (BOUNDARY + defines + "\n" + platform_h + "\n" + definitions + "\n" + pure +
            "\n" + DEVICES + "\n" + function(platform, "ane_fw_preload_range") + "\n" +
            bodies["ane_fw_handoff_present"] + "\n" + bodies["ane_fw_preload_metadata"] +
            "\n" + bodies["ane_fw_boot"] + "\n" + TESTS)


BOUNDARY = r'''
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
#define GENMASK(h,l) ((~0U << (l)) & (~0U >> (31 - (h))))
#define ARRAY_SIZE(x) (sizeof(x) / sizeof((x)[0]))
#define IS_ALIGNED(n,a) (!((n) & ((a) - 1)))
#define check_add_overflow(a,b,c) __builtin_add_overflow((a),(b),(c))
#define lower_32_bits(x) ((u32)(x))
#define upper_32_bits(x) ((u32)((u64)(x) >> 32))
#define __free(x)
#define __iomem
#define SHA256_DIGEST_SIZE 32
#define MEMREMAP_WB 1
#define REGION_DISJOINT 0
#define IORESOURCE_SYSTEM_RAM 1
#define IORES_DESC_NONE 0
struct device;
static bool ane_fw_observe_only;
#define ane_fw_transport_fail(t,e) __ane_fw_transport_fail(t,e,__LINE__)
static void discard_log(const char *fmt, ...) { (void)fmt; }
#define dev_info(dev,...) ((void)(dev), discard_log(__VA_ARGS__))
#define dev_dbg(dev,...) ((void)(dev), discard_log(__VA_ARGS__))
#define dma_wmb() ((void)0)
static u32 get_unaligned_le32(const void *p) {
    const u8 *b = p; return (u32)b[0] | (u32)b[1] << 8 | (u32)b[2] << 16 | (u32)b[3] << 24;
}
static u64 get_unaligned_le64(const void *p) {
    return (u64)get_unaligned_le32(p) | (u64)get_unaligned_le32((const u8 *)p + 4) << 32;
}
static void put_unaligned_le32(u32 v, void *p) {
    u8 *b = p; for (unsigned int i = 0; i < 4; i++) b[i] = v >> (8 * i);
}
static void put_unaligned_le64(u64 v, void *p) {
    put_unaligned_le32(v,p); put_unaligned_le32(v >> 32, (u8 *)p + 4);
}
'''

DEVICES = r'''
struct device_node {
    struct device_node *parent, *regions[2];
    bool available, no_map, reusable, ram, preloaded;
    u64 physical, size, dva[2];
    int region_count, name_count, iova_count;
    const char *names[2], *marker;
    u32 ane_type;
};
struct device { struct device_node *of_node; };
struct platform_device { struct device dev; };
struct resource { u64 start, end; };
struct ane_fw_backend {
    struct platform_device *pdev;
    const struct ane_fw_platform *soc;
    void *engine;
    struct ane_fw_surface pool, ipc, heap;
    struct ane_fw_transport transport;
    bool exclave_mode_stopped;
    const u8 *text_pin;
};
static struct device_node chosen, reserved, node, text_mem, data_mem;
static u8 measured[32], text_byte, engine[0x200], pool[0x4000], ipc_memory[0x14000];
static unsigned int mappings, hashes, stores, waits;
static size_t hashed_bytes;
static bool map_failure;
static u32 requested, channel_count, heap_request, mode_request;
static u64 returned_table;
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
    assert(!strcmp(key,"apple,ane-type")); *out = np->ane_type; return 0;
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
static int of_property_read_u8_array(struct device_node *np, const char *key, u8 *out, int count) {
    (void)np; (void)out;
    assert(!strcmp(key,"apple,preload-data-sha256") && count == 32); return -EINVAL;
}
static int of_property_read_string_index(struct device_node *np, const char *key,
                                          unsigned int i, const char **out) {
    assert(!strcmp(key,"memory-region-names") && i < 2);
    *out = np->names[i]; return *out ? 0 : -EINVAL;
}
static void *memremap(u64 physical, u64 size, int mode) {
    assert(mode == MEMREMAP_WB && physical == text_mem.physical);
    assert(size == 0xec000 || size == 0xc0000);
    mappings++; return map_failure ? NULL : &text_byte;
}
static void memunmap(void *p) { assert(p == &text_byte); }
static void sha256(const void *p, size_t size, u8 *out) {
    assert(p == &text_byte); hashes++; hashed_bytes = size; memcpy(out,measured,32);
}
static u32 ane_rd32(struct ane_fw_backend *b, const void *p) {
    (void)b; assert((const u8 *)p >= engine && (const u8 *)p + 4 <= engine + sizeof(engine));
    return get_unaligned_le32(p);
}
static void ane_wr32(struct ane_fw_backend *b, u32 v, void *p) {
    (void)b; assert((u8 *)p >= engine && (u8 *)p + 4 <= engine + sizeof(engine)); put_unaligned_le32(v,p);
}
static int ane_fw_scratch_wait(struct ane_fw_backend *b, unsigned int cell, u32 wanted) {
    u8 *scratch = (u8 *)b->engine + b->soc->scratch;
    assert(wanted == 0x08042006 || (!wanted && cell == 3));
    waits++;
    if (waits == 1) {
        put_unaligned_le32(channel_count,scratch); put_unaligned_le32(requested,scratch + 4);
        put_unaligned_le32(heap_request,scratch + 12); put_unaligned_le32(mode_request,scratch + 16);
    } else if (waits == 2) {
        put_unaligned_le32(lower_32_bits(returned_table),scratch);
        put_unaligned_le32(upper_32_bits(returned_table),scratch + 4);
    }
    return 0;
}
static u64 boundary_load(void *ctx, const void *p) { (void)ctx; return get_unaligned_le64(p); }
static void boundary_store(void *ctx, void *p, u64 value) {
    struct ane_fw_backend *b = ctx;
    assert((u8 *)p >= ipc_memory && (u8 *)p + 8 <= ipc_memory + b->transport.ipc.size);
    stores++; put_unaligned_le64(value,p);
}
static void boundary_barrier(void *ctx) { (void)ctx; }
static u32 boundary_read(void *ctx, u32 reg) { (void)ctx; (void)reg; return 0; }
static void boundary_write(void *ctx, u32 reg, u32 value) { (void)ctx; (void)reg; (void)value; }
static u64 boundary_now(void *ctx) { (void)ctx; return 0; }
static void boundary_relax(void *ctx, u64 elapsed) { (void)ctx; (void)elapsed; }
static int boundary_alloc(void *ctx, size_t size, u32 tag, struct ane_fw_surface *out) {
    (void)ctx; (void)size; (void)tag; (void)out; assert(false); return -ENOMEM;
}
static void boundary_refused(void *ctx, u64 a, u64 b, u64 c, int error) {
    (void)ctx; (void)a; (void)b; (void)c; (void)error;
}
static const struct ane_fw_transport_ops ane_fw_ops = {
    .load64 = boundary_load, .store64 = boundary_store,
    .read_barrier = boundary_barrier, .write_barrier = boundary_barrier,
    .read32 = boundary_read, .write32 = boundary_write,
    .now_ns = boundary_now, .relax = boundary_relax,
    .alloc = boundary_alloc, .refused = boundary_refused,
};
'''

TESTS = r'''
static void good_handoff(void) {
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
    mappings = hashes = 0; hashed_bytes = 0; map_failure = ane_fw_observe_only = false;
}
static bool handoff(struct ane_fw_handoff *h) {
    struct platform_device p = { .dev.of_node = &node };
    return ane_fw_handoff_present(&p,&ane_t6050_platform,h);
}
static void admission_tests(void) {
    struct ane_fw_handoff h;
    good_handoff(); assert(handoff(&h));
    assert(mappings == 1 && hashes == 1 && hashed_bytes == 0xec000 && h.text_ok);
    assert(h.build == &ane_t6050_builds[1]);
    assert(!memcmp(h.text_digest,ane_t6050_text_pin_27_3,32));
    struct ane_fw_cold_state pmgr = { .subblock = 0x300 };
    h.pmgr = &pmgr; assert(ane_fw_cold_handoff_valid(&h,&ane_t6050_platform));
    pmgr.cpu = 1; assert(!ane_fw_cold_handoff_valid(&h,&ane_t6050_platform));
    pmgr.cpu = 0; h.crash_kernel = true; assert(!ane_fw_cold_handoff_valid(&h,&ane_t6050_platform));
    good_handoff(); ane_fw_observe_only = true;
    assert(!handoff(&h) && hashes == 1 && h.text_ok);
    good_handoff(); chosen.marker = NULL; assert(!handoff(&h) && hashes == 1);
    good_handoff(); measured[0] ^= 1; assert(!handoff(&h) && hashes == 1 && !h.text_ok);
    static const u8 retained_file_pin[32] = {
        0xfc,0xa6,0xa5,0x9a,0xf2,0x0e,0x39,0x64,0x06,0x7d,0x30,0x0e,0xc7,0x5c,0x8c,0x1b,
        0xf8,0x8b,0xfd,0xaf,0x55,0xb3,0x7d,0x2e,0x34,0x80,0x8d,0xd1,0xb7,0x25,0xb9,0x0d };
    good_handoff(); memcpy(measured,retained_file_pin,32); assert(!handoff(&h) && !h.text_ok);
    good_handoff(); map_failure = true; assert(!handoff(&h) && mappings == 1 && !hashes);
    for (unsigned int n = 0; n < 16; n++) {
        good_handoff();
        switch (n) {
        case 0: node.region_count = 3; break;
        case 1: node.name_count = 3; break;
        case 2: node.iova_count = 3; break;
        case 3: node.names[0] = "foreign-text"; break;
        case 4: text_mem.available = false; break;
        case 5: text_mem.no_map = false; break;
        case 6: text_mem.reusable = true; break;
        case 7: data_mem.ram = true; break;
        case 8: text_mem.parent = &chosen; break;
        case 9: text_mem.size = 0x400000000ULL; break;
        case 10: text_mem.physical++; break;
        case 11: data_mem.physical = text_mem.physical + SZ_16K; break;
        case 12: node.dva[1] = 0x100000c0000ULL; break;
        case 13: node.ane_type = 0x200; break;
        case 14: node.preloaded = false; break;
        case 15: text_mem.physical = U64_MAX - SZ_16K + 1; break;
        }
        assert(!handoff(&h) && !mappings && !hashes);
    }
    good_handoff(); text_mem.size = 0xc0000; data_mem.size = 0x2ac000;
    node.dva[1] = 0x100000c0000ULL; memcpy(measured,ane_t6050_text_pin_27_0,32);
    assert(handoff(&h) && h.build == &ane_t6050_builds[0] && hashed_bytes == 0xc0000);
    struct platform_device p = { .dev.of_node = &node };
    struct ane_fw_backend b = { .pdev = &p, .soc = &ane_t6050_platform,
                                .text_pin = ane_t6050_text_pin_27_0 };
    u64 phys[2], size[2], dva[2]; u8 data_pin[32]; bool have;
    assert(!ane_fw_preload_metadata(&b,phys,size,dva,data_pin,&have) && !have);
    good_handoff(); assert(ane_fw_preload_metadata(&b,phys,size,dva,data_pin,&have) == -EINVAL);
}
static void geometry_tests(void) {
    u64 phys[] = { 0x100000000ULL,0x200000000ULL };
    u64 size[] = { 0xec000,0x280000 }, dva[] = { 0x10000000000ULL,0x100000ec000ULL };
    u64 tail, bytes;
    assert(!ane_fw_heap_tail(dva,size,ANE_FW_POOL_DVA,&tail,&bytes));
    assert(tail == 0x1000036c000ULL && bytes == 0x14000);
    size[0] = 0xc0000; size[1] = 0x2ac000; dva[1] = 0x100000c0000ULL;
    assert(!ane_fw_heap_tail(dva,size,ANE_FW_POOL_DVA,&tail,&bytes) && bytes == 0x14000);
    assert(ane_fw_preload_text_pin(&ane_t6050_platform,phys,size,dva) == ane_t6050_text_pin_27_0);
    struct ane_fw_platform single = { .preload_dva = { dva[0],dva[1] },
        .preload_size = { size[0],size[1] }, .text_pin = ane_t6050_text_pin_27_0 };
    assert(ane_fw_preload_text_pin(&single,phys,size,dva) == single.text_pin);
    dva[1]++; assert(!ane_fw_preload_layout_match(dva,size,phys,size,dva));
    dva[1]--; size[0] = 0;
    assert(!ane_fw_preload_layout_match(dva,size,phys,size,dva));
    assert(ane_fw_heap_tail(dva,size,ANE_FW_POOL_DVA,&tail,&bytes) == -ERANGE);
    size[0] = 0xc0000; dva[0] = U64_MAX - SZ_16K + 1;
    assert(!ane_fw_preload_layout_match(dva,size,phys,size,dva));
    assert(ane_fw_heap_tail(dva,size,U64_MAX,&tail,&bytes) == -ERANGE);
    dva[0] = 0x10000000000ULL; dva[1] = dva[0] + SZ_16K;
    assert(!ane_fw_preload_layout_match(dva,size,phys,size,dva));
    assert(ane_fw_heap_tail(dva,size,ANE_FW_POOL_DVA,&tail,&bytes) == -ERANGE);
}
static void make_table(u8 *table, u64 base) {
    static const u32 types[8] = { 2,0,2,0,1,1,1,0 };
    memset(table,0,ANE_FW_TABLE_SIZE);
    for (unsigned int i = 0; i < ANE_FW_CHANNELS; i++) {
        u8 *d = table + i * ANE_FW_DESCRIPTOR_SIZE;
        strcpy((char *)d,ane_fw_channel_name(i));
        put_unaligned_le32(types[i],d + 0x40); put_unaligned_le32(i,d + 0x44);
        put_unaligned_le64(4,d + 0x48); put_unaligned_le64(base + 0x1000 + i * 0x400,d + 0x50);
    }
}
static void parser_tests(void) {
    u8 table[ANE_FW_TABLE_SIZE]; struct ane_fw_channel channels[8], unchanged[8];
    const u64 base = ANE_FW_IPC_DVA;
    make_table(table,base); assert(!ane_fw_channels_parse(table,base,0xc441,base,channels));
    for (unsigned int i = 0; i < 8; i++) assert(channels[i].index == i);
    for (unsigned int n = 0; n < 16; n++) {
        make_table(table,base); memset(channels,0xa5,sizeof(channels)); memcpy(unchanged,channels,sizeof(channels));
        u8 *d = table; size_t bytes = 0xc441; u64 table_dva = base, ipc_dva = base;
        switch (n) {
        case 0: memset(d,'x',64); break;
        case 1: strcpy((char *)d,"IO"); break;
        case 2: strcpy((char *)d,"FOREIGN"); break;
        case 3: put_unaligned_le32(3,d + 0x40); break;
        case 4: put_unaligned_le32(32,d + 0x44); break;
        case 5: put_unaligned_le64(0,d + 0x48); break;
        case 6: put_unaligned_le64(0x401,d + 0x48); break;
        case 7: put_unaligned_le64(U64_MAX,d + 0x48); break;
        case 8: put_unaligned_le64(base + 0x1001,d + 0x50); break;
        case 9: put_unaligned_le64(base - 64,d + 0x50); break;
        case 10: put_unaligned_le64(base + 0xc440,d + 0x50); break;
        case 11: put_unaligned_le64(base + 0x400,d + 0x50); break;
        case 12: put_unaligned_le64(base + 0x1400,d + 0x50); break;
        case 13: bytes = ANE_FW_TABLE_SIZE - 1; break;
        case 14: table_dva = base + 1; break;
        case 15: ipc_dva = U64_MAX - 63; table_dva = ipc_dva; break;
        }
        assert(ane_fw_channels_parse(table,ipc_dva,bytes,table_dva,channels) == -EPROTO);
        assert(!memcmp(channels,unchanged,sizeof(channels)));
    }
    make_table(table,base); put_unaligned_le64(1,table + 0x48);
    put_unaligned_le64(base + 0x10400,table + 0x50);
    assert(!ane_fw_channels_parse(table,base,0x10441,base,channels));
    assert(ane_fw_channels_parse(table,base,0xc441,base,channels) == -EPROTO);
    assert(ane_fw_channels_parse(table,base,0x10441,base + 0x10400,channels) == -EPROTO);
    assert(ane_fw_channels_parse(NULL,base,0x10441,base,channels) == -EPROTO);
    assert(ane_fw_channels_parse(table,base,0x10441,base,NULL) == -EPROTO);
}
static struct ane_fw_backend boot_fixture(struct ane_fw_platform *soc, u32 request) {
    static struct platform_device pdev;
    memset(engine,0,sizeof(engine)); memset(pool,0,sizeof(pool)); memset(ipc_memory,0,sizeof(ipc_memory));
    waits = stores = 0; requested = request; channel_count = 8; heap_request = 0xf00000; mode_request = 0;
    returned_table = ANE_FW_IPC_DVA;
    make_table(ipc_memory,ANE_FW_IPC_DVA);
    return (struct ane_fw_backend){ .pdev = &pdev, .soc = soc, .engine = engine,
        .pool = { .cpu = pool, .dva = ANE_FW_POOL_DVA, .size = sizeof(pool) },
        .ipc = { .cpu = ipc_memory, .dva = ANE_FW_IPC_DVA, .size = sizeof(ipc_memory) },
        .heap = { .dva = ANE_FW_HEAP_DVA, .size = 0xf00000 } };
}
static void boot_tests(void) {
    struct ane_fw_platform soc = ane_t6050_platform; soc.scratch = 0x100; soc.wrapper = 0;
    for (unsigned int n = 0; n < 2; n++) {
        const u32 request = n ? 0x10440 : 0xc440;
        struct ane_fw_backend b = boot_fixture(&soc,request);
        assert(!ane_fw_boot(&b));
        assert(get_unaligned_le64(pool + 0x180 + 0x50) == (u64)request + 1);
        assert(b.ipc.size == 0x14000 && b.transport.ipc.size == (u64)request + 1);
        assert(stores && b.transport.armed);
        ipc_memory[0] ^= 1; assert(ane_fw_transport_check(&b.transport) == -EPROTO);
    }
    const u32 invalid[] = { 0, ANE_FW_TABLE_SIZE - 2, 0x14000, UINT32_MAX };
    for (unsigned int i = 0; i < ARRAY_SIZE(invalid); i++) {
        struct ane_fw_backend b = boot_fixture(&soc,invalid[i]);
        assert(ane_fw_boot(&b) == -EPROTO && !stores && waits == 1);
    }
    for (unsigned int i = 0; i < 5; i++) {
        struct ane_fw_backend b = boot_fixture(&soc,0xc440);
        if (i == 0) channel_count = 7;
        if (i == 1) heap_request--;
        if (i == 2) mode_request = 1;
        if (i == 3) returned_table += 64;
        if (i == 4) put_unaligned_le64(ANE_FW_IPC_DVA + 0xc440,ipc_memory + 0x50);
        assert(ane_fw_boot(&b) == -EPROTO && !stores);
    }
}
int main(void) {
    admission_tests(); geometry_tests(); parser_tests(); boot_tests();
    puts("PASS: actual ANE admission, geometry, full-table/ring bounds and firmware-requested IPC");
    return 0;
}
'''


def no_core():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", type=Path, required=True, help="fresh patched kernel source tree")
    args = parser.parse_args()
    controls = (None, "mapped-window", "unbounded-observation", "no-text-pin",
                "wrong-init-size", "observation-start")
    with tempfile.TemporaryDirectory(prefix="ane-27_3-host-") as temporary:
        directory = Path(temporary)
        for mutation in controls:
            source, executable = directory / "check.c", directory / "check"
            source.write_text(harness(args.kernel, mutation))
            subprocess.run([*shlex.split(os.environ.get("CC", "cc")), "-std=gnu11", "-O2",
                            "-Wall", "-Wextra", "-Werror", "-o", str(executable), str(source)],
                           check=True, timeout=60)
            result = subprocess.run([str(executable)], capture_output=True, text=True,
                                    timeout=10, preexec_fn=no_core)
            if mutation is None:
                if result.returncode:
                    raise RuntimeError(result.stdout + result.stderr)
                print(result.stdout.strip())
            elif result.returncode != -6:
                raise RuntimeError(f"negative control {mutation} did not fail an assertion: "
                                   f"{result.returncode}\n{result.stdout}{result.stderr}")
            else:
                print("PASS: negative control rejected:", mutation)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
