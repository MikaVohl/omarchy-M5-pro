#!/usr/bin/env python3
"""Host regressions of production AVE 26C5035r admission and compatibility writers.

Use only a disposable kernel tree with kernel-j716s-ave-27_3.patch applied.
Extracts actual probe, reservations, DART checks, snapshot copy, layout,
Start/Process builders and HEVC slot-header selection. Only DT/MMIO/allocator
boundaries are synthetic. No device access, firmware or hardware qualification.
Native Process lengths must fail without touching the command buffer.
"""
import argparse
import os
from pathlib import Path
import re
import resource
import subprocess
import tempfile

PROJECT = Path(__file__).resolve().parents[1]


def function(source, name):
    match = re.search(r"^(?:static )?(?:const )?[\w *]+\b" + re.escape(name) +
                      r"\([^;]+?\n\{", source, re.M)
    if not match:
        raise ValueError("missing production function: " + name)
    end = source.index("\n}", match.end()) + 2
    return source[match.start():end] + "\n"


def declaration(source, name):
    match = re.search(r"^(?:static const )?(?:struct|enum) " + re.escape(name) + r" \{", source, re.M)
    if not match:
        raise ValueError("missing production declaration: " + name)
    end = source.index("\n};", match.end()) + 3
    return source[match.start():end] + "\n"


def initializer(source, name):
    match = re.search(r"^static const [^\n]+\b" + re.escape(name) + r"(?:\[\])? = \{", source, re.M)
    if not match:
        raise ValueError("missing production initializer: " + name)
    end = source.index("\n};", match.end()) + 3
    return source[match.start():end] + "\n"


def no_includes(source):
    return re.sub(r"^#include .*\n", "", source, flags=re.M)


def harness(kernel, mutation):
    src = kernel / "drivers/media/platform/apple/ave"
    header = (src / "ave.h").read_text()
    fw = (src / "ave-fw.c").read_text()
    drv = (src / "ave-drv.c").read_text()
    code = "#define TEST_PER_ABI 1\n" + (src / "tests/shim.h").read_text()
    # Reuse the existing AVE host boundary and real type declarations, not a
    # second codec implementation. Firmware admission extends its device only.
    for line in header.splitlines():
        if line.startswith("#define AVE_") and not line.startswith(("#define AVE_BUF_", "#define AVE_RING_")):
            code += line + "\n"
    code += "typedef uint64_t phys_addr_t;\n"
    code += "#define GENMASK(h, l) ((~0UL << (l)) & (~0UL >> (8 * sizeof(unsigned long) - 1 - (h))))\n"
    code += no_includes((src / "ave-cmp420.h").read_text())
    for name in ("ave_codec", "ave_buf", "ave_fw_segment", "ave_fw_state", "ave_image", "ave_opcode",
                 "ave_coproc_regs", "ave_fw_build", "ave_variant", "ave_params", "ave_slot", "ave_session"):
        code += declaration(header, name)
    code += no_includes((src / "ave-layout.h").read_text())
    code += no_includes((src / "ave-layout.c").read_text())
    code += no_includes((src / "ave-regs.h").read_text())
    code += no_includes((src / "ave-sizes.h").read_text())
    boundary = (src / "tests/boundary.h").read_text()
    boundary = boundary.replace("struct ave_dev {", "struct device_node; struct device { struct device_node *of_node; };\nstruct ave_dev {\n"
                                "struct ave_fw_segment text, data; struct iommu_domain *domain; void *data_pristine;\n")
    boundary = boundary.replace("void *dev;", "struct device *dev;")
    code += boundary
    code += no_includes((src / "ave-image.c").read_text())
    code += no_includes((src / "ave-writer.c").read_text())
    code += no_includes((src / "ave-abi.c").read_text())
    code += function((src / "ave-sess.c").read_text(), "ave_slot_shdr")
    code += PROBE_BOUNDARY
    code += "static const struct ave_coproc_regs ave_coproc_v40;\n"
    code += initializer(drv, "ave_t6050_builds") + initializer(drv, "ave_t6050")
    known = re.search(r"static const u32 ave_known_text_crc\[\] = \{.*?\n};", fw, re.S)
    if not known:
        raise ValueError("missing production firmware CRC ledger")
    code += known[0] + "\n"
    for name in ("ave_fw_chosen_crc", "ave_fw_segment", "ave_fw_check_mapping", "ave_fw_crc",
                 "ave_fw_text_id_crc", "ave_fw_text_id", "ave_fw_find_build", "ave_fw_probe"):
        code += function(fw, name)
    code += TESTS
    if mutation == "geometry-crc-pair":
        old = "want_crc = build->text_crc;"
        new = "want_crc = 0;"
    elif mutation == "mapping-ownership":
        old = "if (ave_fw_check_mapping(ave, &ave->text) ||"
        new = "if (false ||"
    elif mutation == "pristine-data":
        old = "if (ret || data_crc != want) {"
        new = "if (ret) {"
    elif mutation == "native-length-padding":
        old = ".bytes = { { 0x3b40, 0x6df8 }, { 0x13ee0, 0x78b8 } },"
        new = ".bytes = { { 0x3b40, 0x6e08 }, { 0x13ee0, 0x78c8 } },"
    elif mutation == "hevc-null-header":
        old = "return s->extra.cpu ? &s->extra : &s->aux;"
        new = "return &s->aux;"
    elif mutation is None:
        return code
    else:
        raise ValueError("unknown mutation")
    if code.count(old) != 1:
        raise ValueError("negative control does not match exactly: " + mutation)
    return code.replace(old, new)


PROBE_BOUNDARY = r'''
/* DT, mapping, CRC and allocator boundary. No real memory/device mappings. */
typedef uint32_t __be32;
#define PHYS_ADDR_MAX UINT64_MAX
#define U64_MAX UINT64_MAX
#define __free(x)
#define MEMREMAP_WB 0
#define lower_32_bits(x) ((u32)(x))
#define be32_to_cpup(p) (*(p))
struct device_node { struct device_node *parent; u32 phandle; int index; };
struct resource { u64 start, end; };
static struct device_node reserved, chosen;
static struct device_node nodes[2] = { { &reserved, 99, 0 }, { &reserved, 99, 1 } };
static struct device_node engine = { NULL, 99, 2 };
static struct device device = { &engine };
static size_t text_size;
static u32 text_crc, text_want, data_crc, data_want;
static bool reservation_ok, map_ok, loader_present;
static unsigned int allocations, frees, mappings;
static u8 text_image[0x158000], data_image[0x134000];
static u64 text_phys = 0x10000000, data_phys = 0x11000000;
static u64 text_iova = 0x10000000000ULL, data_iova;
static struct device_node *of_find_node_by_path(const char *path) { return loader_present ? &chosen : NULL; }
static void of_node_put(struct device_node *node) { }
static int of_property_read_string(struct device_node *node, const char *name, const char **value)
{
    static char text[16], data[16];
    snprintf(text, sizeof(text), "%x", text_want); snprintf(data, sizeof(data), "%x", data_want);
    *value = strstr(name, "text") ? text : data; return 0;
}
static int kstrtou32(const char *value, unsigned int base, u32 *out) { *out = strtoul(value, NULL, base); return 0; }
static int of_count_phandle_with_args(struct device_node *node, const char *name, void *arg) { return 2; }
static struct device_node *of_parse_phandle(struct device_node *node, const char *name, unsigned int i) { return i < 2 ? &nodes[i] : NULL; }
static bool of_node_name_eq(struct device_node *node, const char *name) { return node == &reserved; }
static bool of_device_is_compatible(struct device_node *node, const char *name) { return reservation_ok; }
static bool of_property_read_bool(struct device_node *node, const char *name) { return reservation_ok; }
static int of_address_to_resource(struct device_node *node, int i, struct resource *res)
{
    res->start = node->index ? data_phys : text_phys;
    res->end = res->start + (node->index ? sizeof(data_image) : text_size) - 1; return 0;
}
static size_t resource_size(struct resource *res) { return res->end - res->start + 1; }
static const __be32 *of_get_property(struct device_node *node, const char *name, int *len)
{
    static u32 prop[5]; u64 iova = node->index ? data_iova : text_iova;
    u64 size = node->index ? sizeof(data_image) : text_size;
    prop[0] = engine.phandle; prop[1] = iova >> 32; prop[2] = iova;
    prop[3] = size >> 32; prop[4] = size; *len = sizeof(prop); return prop;
}
static u64 of_read_number(const __be32 *p, int count) { assert(count == 2); return ((u64)p[0] << 32) | p[1]; }
static u64 iommu_iova_to_phys(struct iommu_domain *domain, u64 iova)
{
    if (!map_ok && iova == text_iova) return 0;
    if (iova >= text_iova && iova < text_iova + text_size) return text_phys + iova - text_iova;
    if (iova >= data_iova && iova < data_iova + sizeof(data_image)) return data_phys + iova - data_iova;
    return 0;
}
static void *memremap(u64 phys, size_t bytes, int flags)
{
    mappings++; if (phys == text_phys && bytes == text_size) return text_image;
    if (phys == data_phys && bytes == sizeof(data_image)) return data_image;
    return NULL;
}
static void memunmap(void *p) { }
static void *vmalloc(size_t size) { allocations++; return malloc(size); }
static void vfree(void *p) { if (p) frees++; free(p); }
static int dev_err_probe(struct device *dev, int err, const char *fmt, ...) { return err; }
/* CRC is an input boundary for preloads; smaller TEXT-link-cell regressions
 * below use the existing host CRC primitive, not this configured result. */
static u32 probe_crc32(u32 seed, const void *p, size_t bytes)
{
    if (p == text_image && bytes == text_size) return text_crc ^ ~0U;
    if (p == data_image && bytes == sizeof(data_image)) return data_crc ^ ~0U;
    return crc32_le(seed, p, bytes);
}
#define crc32_le probe_crc32
'''

TESTS = r'''
static void setup(size_t bytes, u32 crc)
{
    text_size = bytes; text_crc = text_want = crc; data_crc = data_want = 0xf4e9dda8;
    reservation_ok = map_ok = loader_present = true; allocations = frees = mappings = 0;
    text_phys = 0x10000000; data_phys = 0x11000000;
    data_iova = text_iova + text_size;
    memset(data_image, 0x5a, sizeof(data_image));
}
static int probe(int expected)
{
    struct ave_dev ave = { .dev = &device, .variant = &ave_t6050 };
    int result = ave_fw_probe(&ave);
    assert(result == expected);
    if (!result) {
        assert(ave.layout == &ave_layout_26a428); assert(ave.fw_state == AVE_FW_OFF);
        assert(ave.data_pristine && !memcmp(ave.data_pristine, data_image, sizeof(data_image)));
    } else { assert(!ave.data_pristine); }
    vfree(ave.data_pristine); return result;
}
static void admission(void)
{
    setup(0x154000, 0x87279429); probe(0);
    setup(0x158000, 0xc25b4ddb); probe(0);
    setup(0x154000, 0xc25b4ddb); probe(-ENODEV); assert(!allocations);
    setup(0x158000, 0x87279429); probe(-ENODEV); assert(!allocations);
    setup(0x15c000, 0xc25b4ddb); probe(-EINVAL); assert(!mappings && !allocations);
    setup(0x158000, 0xc25b4ddb); reservation_ok = false; probe(-EINVAL); assert(!mappings && !allocations);
    setup(0x158000, 0xc25b4ddb); map_ok = false; probe(-EIO); assert(!mappings && !allocations);
    setup(0x158000, 0xc25b4ddb); text_want ^= 1; probe(-ENODEV); assert(!allocations);
    setup(0x158000, 0xc25b4ddb); loader_present = false; probe(-ENODEV); assert(!allocations);
    setup(0x158000, 0xc25b4ddb); data_crc ^= 1; probe(-ENODEV); assert(allocations == 1 && frees == 1);
    setup(0x158000, 0xc25b4ddb); data_iova = text_iova; probe(-EINVAL); assert(!mappings && !allocations);
    setup(0x158000, 0xc25b4ddb); data_phys = text_phys; probe(-EINVAL); assert(!mappings && !allocations);
    setup(0x158000, 0xc25b4ddb); data_phys++; probe(-EINVAL); assert(!mappings && !allocations);
    u8 cell[32] = {}; u32 crc;
    assert(ave_fw_text_id_crc(cell, sizeof(cell), 32, 0x1234000, &crc) == -EINVAL);
    put_unaligned_le64(0x1234000, cell + 8);
    assert(!ave_fw_text_id_crc(cell, sizeof(cell), 8, 0x10001234000ULL, &crc));
    cell[8] ^= 1;
    assert(ave_fw_text_id_crc(cell, sizeof(cell), 8, 0x10001234000ULL, &crc) == -ENODEV);
}
static void commands(void)
{
    struct ave_dev ave = { .variant = &ave_t6050, .layout = ave_layout_for_crc(0xc25b4ddb), .dev = &device };
    struct ave_session sess = {}; struct ave_ctx ctx = { .ave = &ave, .sess = &sess };
    assert(ave.layout == &ave_layout_26a428);
    for (unsigned int codec = 1; codec <= 2; codec++) {
        const struct ave_proc_layout *l = &ave.layout->process[codec - 1];
        size_t bytes = codec == 1 ? 0x6df8 : 0x78b8;
        u8 cmd[0x13ee0 + 32];
        memset(&sess, 0, sizeof(sess)); sess.p.codec = codec;
        sess.p.width = sess.p.cw = 1920; sess.p.height = 1080; sess.p.ch = 1088;
        sess.p.stride = 1920; sess.p.fps_num = sess.p.fps_int = 30; sess.p.fps_den = 1;
        sess.p.bit_depth = 8; sess.p.sample_bytes = 1; sess.p.slots = 5; sess.p.refs = 5;
        sess.p.bframes = true; sess.p.gop = 30; sess.p.rc_mode = 4; sess.p.bitrate = 4000000;
        sess.p.max_qp = 51; sess.p.coded_cap = 0x300000;
        sess.p.profile = codec == 1 ? 4 : 1; sess.p.level = codec == 1 ? 12 : 6;
        sess.slot[0].coded.dma = 0x10012340000ULL; sess.slot[0].meta.dma = 0x10012640000ULL;
        sess.slot[0].meta.size = 0x10000; sess.slot[0].extra.dma = 0x10012650000ULL;
        sess.slot[0].extra.cpu = cmd; sess.slot[0].extra.size = 0x4000;
        assert(ave_slot_shdr(&sess.slot[0]) == &sess.slot[0].extra);
        memset(cmd, 0xa5, sizeof(cmd));
        assert(!ave_build_process(&ctx, cmd, bytes, 0, 0x10002000000ULL, sess.p.bitrate, false, false));
        assert(get_unaligned_le64(cmd + l->y) == 0x10002000000ULL);
        assert(get_unaligned_le64(cmd + l->uv) == 0x10002000000ULL + 1920 * 1080);
        assert(get_unaligned_le64(cmd + l->extra) == sess.slot[0].extra.dma);
        assert(get_unaligned_le32(cmd + l->coded_cap) == sess.p.coded_cap);
        assert(get_unaligned_le32(cmd + l->in_w) == 1920 && get_unaligned_le32(cmd + l->in_h) == 1080);
        for (size_t i = bytes; i < sizeof(cmd); i++) assert(cmd[i] == 0xa5);
        memset(cmd, 0xa5, sizeof(cmd));
        assert(ave_build_process(&ctx, cmd, bytes + 16, 0, 0x10002000000ULL, sess.p.bitrate, false, false) == -EINVAL);
        for (size_t i = 0; i < sizeof(cmd); i++) assert(cmd[i] == 0xa5);
        assert(ave_build_process(&ctx, cmd, bytes, 5, 0x10002000000ULL, sess.p.bitrate, false, false) == -EINVAL);
        assert(ave_build_process(&ctx, cmd, bytes, 0, 0x10002000000ULL, sess.p.bitrate, true, false) == -EOPNOTSUPP);
        assert(ave_build_process(&ctx, cmd, bytes, 0, 0x10002000000ULL, sess.p.bitrate + 1, false, false) == -EOPNOTSUPP);
        size_t start = codec == 1 ? 0x3b40 : 0x13ee0;
        memset(cmd, 0xa5, sizeof(cmd)); assert(!ave_build_start(&ctx, cmd, start));
        for (size_t i = start; i < sizeof(cmd); i++) assert(cmd[i] == 0xa5);
    }
}
int main(void) { admission(); commands(); puts("PASS: paired builds, loader/DART ownership, DATA snapshot, compatibility writer bounds and HEVC header source"); }
'''

POSTBOOT_RECIPE = r'''# Printed only; never executed by this host regression runner.
# Run attended ONLY after the exact candidate kernel/DT/modules and pristine
# loader handoff are verified. Unknown native extensions are NOT being tested.
# Stop after the first failure; no retry, unbind, reset or power-cycle recovery.
set -euo pipefail # bash
umask 077
AVE_DEVICE=
for name in /sys/class/video4linux/video*/name; do
    if [ "$(cat "$name")" = apple-ave ]; then
        [ -z "$AVE_DEVICE" ] || { echo 'Multiple AVE nodes; refused' >&2; exit 1; }
        AVE_DEVICE=/dev/$(basename "$(dirname "$name")")
    fi
done
[ -n "$AVE_DEVICE" ] || { echo 'No apple-ave node; refused' >&2; exit 1; }
v4l2-ctl --device="$AVE_DEVICE" --all
gst-inspect-1.0 v4l2h264enc >/dev/null
gst-inspect-1.0 v4l2h265enc >/dev/null
gst-inspect-1.0 avdec_h264 >/dev/null
gst-inspect-1.0 avdec_h265 >/dev/null
factory_device() {
    LC_ALL=C gst-inspect-1.0 "$1" | awk '
        /^  device[[:space:]]*:/ { device=1; next }
        device && /Default: / { sub(/^.*Default: "/, ""); sub(/".*$/, ""); print; exit }
    '
}
# Dynamic encoder factories bind their device at registration; it is read-only.
[ "$(factory_device v4l2h264enc)" = "$AVE_DEVICE" ]
[ "$(factory_device v4l2h265enc)" = "$AVE_DEVICE" ]
EVIDENCE=$(mktemp -d "$HOME/ave-compat-26C5035r-XXXXXXXX")
START=$(date --iso-8601=seconds)
trap 'journalctl -k --since="$START" --no-pager > "$EVIDENCE/kernel.txt"' EXIT
check_kernel() {
    journalctl -k --since="$START" --no-pager > "$EVIDENCE/kernel.txt"
    if grep -Ei 'DART.*fault|timed? ?out|SError|Oops|BUG:|quarantin|broken' "$EVIDENCE/kernel.txt"; then
        echo 'Kernel error or quarantine observed; refused runtime pass' >&2
        exit 1
    fi
}
timeout --foreground 120s gst-launch-1.0 -e \
    videotestsrc pattern=ball num-buffers=30 ! \
    video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 ! tee name=t \
    t. ! queue ! filesink async=false location="$EVIDENCE/h264-reference.nv12" \
    t. ! queue ! v4l2h264enc \
    extra-controls="controls,video_bitrate=4000000,video_b_frames=3,video_gop_size=30" ! \
    video/x-h264,profile=main ! h264parse ! filesink location="$EVIDENCE/compat.h264" \
    2>&1 | tee "$EVIDENCE/h264-encode.txt"
timeout --foreground 120s gst-launch-1.0 -e \
    filesrc location="$EVIDENCE/compat.h264" ! h264parse ! avdec_h264 ! \
    videoconvert ! video/x-raw,format=NV12,width=1920,height=1080 ! \
    filesink location="$EVIDENCE/h264-decoded.nv12" 2>&1 | tee "$EVIDENCE/h264-decode.txt"
[ "$(stat -c %s "$EVIDENCE/h264-decoded.nv12")" = 93312000 ]
check_kernel
timeout --foreground 120s gst-launch-1.0 -e \
    videotestsrc pattern=ball num-buffers=30 ! \
    video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 ! tee name=t \
    t. ! queue ! filesink async=false location="$EVIDENCE/hevc-reference.nv12" \
    t. ! queue ! v4l2h265enc \
    extra-controls="controls,video_bitrate=4000000,video_b_frames=3,video_gop_size=30" ! \
    video/x-h265,profile=main ! h265parse ! filesink location="$EVIDENCE/compat.hevc" \
    2>&1 | tee "$EVIDENCE/hevc-encode.txt"
timeout --foreground 120s gst-launch-1.0 -e \
    filesrc location="$EVIDENCE/compat.hevc" ! h265parse ! avdec_h265 ! \
    videoconvert ! video/x-raw,format=NV12,width=1920,height=1080 ! \
    filesink location="$EVIDENCE/hevc-decoded.nv12" 2>&1 | tee "$EVIDENCE/hevc-decode.txt"
[ "$(stat -c %s "$EVIDENCE/hevc-decoded.nv12")" = 93312000 ]
check_kernel
# FFmpeg is used only for CPU comparison, never its unsupported V4L2 encoder.
for codec in h264 hevc; do
    [ "$(stat -c %s "$EVIDENCE/$codec-reference.nv12")" = 93312000 ]
    timeout --foreground 30s ffmpeg -hide_banner -loglevel error \
        -f rawvideo -pixel_format nv12 -video_size 1920x1080 -framerate 30 \
        -i "$EVIDENCE/$codec-reference.nv12" \
        -f rawvideo -pixel_format nv12 -video_size 1920x1080 -framerate 30 \
        -i "$EVIDENCE/$codec-decoded.nv12" \
        -lavfi "psnr=stats_file=$EVIDENCE/$codec-psnr.txt" -frames:v 30 -f null -
done
python3 - "$EVIDENCE" <<'PY'
from pathlib import Path
import math, sys
root = Path(sys.argv[1])
for codec in ("h264", "hevc"):
    rows = [dict(field.split(":", 1) for field in line.split())
            for line in (root / (codec + "-psnr.txt")).read_text().splitlines()]
    assert len(rows) == 30 and [int(row["n"]) for row in rows] == list(range(1, 31))
    values = [float(row[key]) for row in rows for key in ("psnr_avg", "psnr_y", "psnr_u", "psnr_v")]
    assert all(not math.isnan(value) and value >= 35 for value in values), (codec, min(values))
    print(codec, "30 decoded frames, minimum per-frame/channel PSNR", min(values), "dB")
PY
echo "30 frames hardware-encoded and software-decoded per codec, pixel comparison passed; evidence: $EVIDENCE"
# This proves only one 1080p/30 NV12 Main compatibility session per codec,
# not native ABI, Main10, low delay, suspend or concurrency.
'''


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel-source", type=Path)
    parser.add_argument("--postboot-recipe", action="store_true", help="print commands, never execute them")
    args = parser.parse_args()
    if args.postboot_recipe:
        print(POSTBOOT_RECIPE)
        return
    if args.kernel_source is None:
        parser.error("--kernel-source is required for host regressions")
    mutations = ("geometry-crc-pair", "mapping-ownership", "pristine-data", "native-length-padding", "hevc-null-header")
    with tempfile.TemporaryDirectory(prefix="j716s-ave-27_3-") as temporary:
        root = Path(temporary)
        for mutation in (None,) + mutations:
            source, exe = root / "ave.c", root / "ave"
            source.write_text(harness(args.kernel_source, mutation))
            subprocess.run([os.environ.get("CC", "cc"), "-std=gnu11", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                            "-Wno-unused-parameter", "-Wno-unused-function", "-Wno-unused-variable",
                            "-fsanitize=address,undefined", "-fno-omit-frame-pointer", str(source), "-o", str(exe)], check=True)
            result = subprocess.run([str(exe)], capture_output=True, text=True, check=False)
            if mutation is None:
                if result.returncode:
                    raise SystemExit(result.stdout + result.stderr)
                print(result.stdout, end="")
            elif not result.returncode or "assert" not in result.stderr.lower():
                raise SystemExit("negative control did not fail by assertion: " + mutation + "\n" + result.stderr)
            else:
                print("PASS: assertion rejected " + mutation)


if __name__ == "__main__":
    main()
