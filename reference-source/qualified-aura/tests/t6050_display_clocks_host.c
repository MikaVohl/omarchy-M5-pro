/* SPDX-License-Identifier: MIT */
/* Consumer-visible failure cases: predeclared providers must not block DCP publication;
 * new providers must still work; wrong provider type, clock-cell count or missing phandle
 * must refuse publication before any display node becomes available. Hardware ADT/DART
 * admission is deliberately outside this host publication test and remains a real-boot gate.
 */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "kboot_display_handoff.c"

struct fw_version_info os_firmware = {V27_3B1, "27.3", {27, 3, 0}, 3, "mBoot"};

/* The fixture has no unit-address-ambiguous parent. Use libfdt for its path lookup. */
int kboot_fdt_path_exact(const void *dt, const char *path)
{
    return fdt_path_offset(dt, path);
}

int firmware_set_fdt(void *dt, int node, const char *name, const struct fw_version_info *version)
{
    fdt32_t value[5];
    for (size_t i = 0; i < version->num_length; i++)
        value[i] = cpu_to_fdt32(version->num[i]);
    return fdt_setprop(dt, node, name, value, version->num_length * sizeof(value[0]));
}

static void add_node(void *dt, const char *parent, const char *name)
{
    assert(fdt_add_subnode(dt, fdt_path_offset(dt, parent), name) >= 0);
}

static void set_u32(void *dt, const char *path, const char *name, u32 value)
{
    assert(!fdt_setprop_u32(dt, fdt_path_offset(dt, path), name, value));
}

static u32 get_u32(const void *dt, const char *path, const char *name)
{
    int length;
    const fdt32_t *value = fdt_getprop(dt, fdt_path_offset(dt, path), name, &length);
    assert(value && length == 4);
    return fdt32_ld(value);
}

static void setup(void *dt, const char *scenario)
{
    assert(!fdt_create_empty_tree(dt, 65536));
    add_node(dt, "/", "chosen");
    add_node(dt, "/", "reserved-memory");
    add_node(dt, "/", "soc");
    add_node(dt, "/", "display-subsystem");
    add_node(dt, "/soc", "dcp@48ae00000");
    add_node(dt, "/soc/dcp@48ae00000", "panel");
    add_node(dt, "/soc/dcp@48ae00000", "piodma");
    add_node(dt, "/soc", "mailbox@1");
    add_node(dt, "/soc", "iommu@2");
    add_node(dt, "/soc", "iommu@3");
    memset(&t6050, 0, sizeof(t6050));
    const char *paths[T6050_N] = {"/soc/dcp@48ae00000", "/display-subsystem", "/soc/mailbox@1",
                                 "/soc/iommu@2", "/soc/iommu@3", "/soc/dcp@48ae00000/piodma"};
    for (size_t i = 0; i < T6050_N; i++) {
        strcpy(t6050.path[i], paths[i]);
        assert(!fdt_setprop_string(dt, fdt_path_offset(dt, paths[i]), "status", "disabled"));
    }
    t6050.pmc = 0x286700000;
    t6050.pmc_size = 0x114000;
    t6050.bw_offset = 0x20000;
    t6050.vram = 0x10bd1000000;
    t6050.vram_size = 0x4000000;
    t6050.nits = 600;
    t6050.nclk = 2;
    t6050.clk_id[0] = 361;
    t6050.clk_id[1] = 458;
    t6050.clk_hz[0] = 810000000;
    t6050.clk_hz[1] = 521064992;
    t6050.uuid = T6050_DCP_26C_UUID;
    t6050.compat = &t6050_compat_26c;
    if (!strcmp(scenario, "new"))
        return;
    for (u32 i = 0; i < 2; i++) {
        char name[64], path[65];
        snprintf(name, sizeof(name), "clock-dcp-firmware-%u", t6050.clk_id[i]);
        snprintf(path, sizeof(path), "/%s", name);
        add_node(dt, "/", name);
        assert(!fdt_setprop_string(dt, fdt_path_offset(dt, path), "compatible", "fixed-clock"));
        set_u32(dt, path, "#clock-cells", 0);
        set_u32(dt, path, "phandle", 10 + i);
        set_u32(dt, path, "clock-frequency", t6050.clk_hz[i]);
    }
    const char *first = "/clock-dcp-firmware-361";
    if (!strcmp(scenario, "wrong-type"))
        assert(!fdt_setprop_string(dt, fdt_path_offset(dt, first), "compatible", "not-a-fixed-clock"));
    if (!strcmp(scenario, "wrong-cells"))
        set_u32(dt, first, "#clock-cells", 1);
    if (!strcmp(scenario, "missing-phandle"))
        assert(!fdt_delprop(dt, fdt_path_offset(dt, first), "phandle"));
}

int main(void)
{
    const char *scenarios[] = {"new", "existing", "wrong-type", "wrong-cells", "missing-phandle"};
    for (size_t s = 0; s < sizeof(scenarios) / sizeof(scenarios[0]); s++) {
        void *dt = malloc(65536);
        assert(dt);
        setup(dt, scenarios[s]);
        int result = t6050_publish(dt);
        bool accepted = s < 2;
        assert((result == 0) == accepted);
        for (size_t i = 0; i < T6050_PIODMA; i++) {
            const char *status = fdt_getprop(dt, fdt_path_offset(dt, t6050.path[i]), "status", NULL);
            assert(status && !strcmp(status, accepted ? "okay" : "disabled"));
        }
        if (accepted) {
            int length;
            const fdt32_t *clocks = fdt_getprop(dt, fdt_path_offset(dt, t6050.path[T6050_DCP]), "clocks", &length);
            assert(clocks && length == 8);
            for (u32 i = 0; i < 2; i++) {
                char path[65];
                snprintf(path, sizeof(path), "/clock-dcp-firmware-%u", t6050.clk_id[i]);
                u32 ph = fdt_get_phandle(dt, fdt_path_offset(dt, path));
                assert(ph && fdt32_ld(&clocks[i]) == ph);
                assert(get_u32(dt, path, "clock-frequency") == t6050.clk_hz[i]);
                if (s == 1)
                    assert(ph == 10 + i);
            }
            const char *status = fdt_getprop(dt, fdt_path_offset(dt, "/chosen"), T6050_STATUS, NULL);
            assert(status && !strcmp(status, "published"));
        }
        printf("PASS T6050 clock publication: %s\n", scenarios[s]);
        free(dt);
    }
    return 0;
}
