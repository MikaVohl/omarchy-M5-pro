#!/usr/bin/env python3
"""Exercise pinned DP-tunnel production bodies without hardware or DMA.

Run once after integration: python3 -B scripts/test-peripherals-kernel.py
Requires the immutable source archive, patch(1) and cc. Source extraction and
compilation use a temporary directory; existing source/build trees are untouched.
"""
import hashlib
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "8ab10100a4c1c944797e3fc8345589aff57777df"
ARCHIVE = ROOT / ("work/sources/kernel-" + COMMIT + ".tar.gz")
ARCHIVE_SHA256 = "06acab85a9e668010fe9e664d9fc92a15d506a986faab8b9659edd4bbcaaeac2"
FILES = (
    "drivers/gpu/drm/apple/apple_drv.c", "drivers/gpu/drm/apple/dcp.c",
    "drivers/thunderbolt/apple.c", "include/linux/soc/apple/dp-tunnel.h",
    "drivers/thunderbolt/apple-dpin-state.c", "drivers/thunderbolt/apple-dpin-state.h",
)


def production_sources():
    with tempfile.TemporaryDirectory(prefix="j716s-peripheral-source-") as temporary:
        directory = Path(temporary)
        with ARCHIVE.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != ARCHIVE_SHA256:
                raise ValueError("kernel archive differs from the immutable pin")
            stream.seek(0)
            with tarfile.open(fileobj=stream, mode="r:gz") as archive:
                for relative in FILES:
                    member = archive.getmember("linux-" + COMMIT + "/" + relative)
                    if not member.isfile():
                        raise ValueError("production source is not a regular member: " + relative)
                    destination = directory / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(archive.extractfile(member).read())
        subprocess.run(["patch", "--batch", "--fuzz=0", "-p1", "-i",
                        str(ROOT / "patches/kernel-j716s-peripherals.patch")],
                       cwd=directory, check=True)
        return {relative: (directory / relative).read_text() for relative in FILES}


def function(source, name):
    match = re.search(r"^(?:static )?(?:bool|void|unsigned long)\s+" +
                      re.escape(name) + r"\([^;]*?\n\{", source, re.MULTILINE)
    if not match:
        raise AssertionError("missing production function: " + name)
    opener = source.index("{", match.start())
    depth, end = 1, opener + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end]


def record(source, kind, name):
    match = re.search(r"^" + kind + " " + re.escape(name) + r" \{.*?^\};",
                      source, re.MULTILINE | re.DOTALL)
    if not match:
        raise AssertionError("missing production record: " + name)
    return match[0]


PRELUDE = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <limits.h>
#include <string.h>
typedef uint32_t u32;
typedef uint64_t u64;
struct mutex { unsigned int rank; bool held; };
static unsigned int ranks[3], depth, refs, symbol_gets, symbol_puts, waits;
static unsigned long jiffies, epoch, finish_after, withdraw_after, largest_wait;
static bool provider_available;
static int withdrawal;
static struct mutex *mutex_acquire(struct mutex *lock)
{
    assert(!lock->held && depth < 3);
    assert(!depth || ranks[depth - 1] < lock->rank);
    ranks[depth++] = lock->rank;
    lock->held = true;
    return lock;
}
static void mutex_release(struct mutex **lock)
{
    assert((*lock)->held && depth && ranks[depth - 1] == (*lock)->rank);
    --depth;
    (*lock)->held = false;
}
#define guard(type) struct mutex *__attribute__((cleanup(mutex_release))) held_lock = mutex_acquire
#define symbol_get(name) (symbol_gets++, provider_available ? (refs++, &name) : (typeof(&name))0)
#define symbol_put(name) do { assert(refs); --refs; ++symbol_puts; } while (0)
#define min_t(type, a, b) ((type)(a) < (type)(b) ? (type)(a) : (type)(b))
#define max_t(type, a, b) ((type)(a) > (type)(b) ? (type)(a) : (type)(b))
#define msecs_to_jiffies(ms) ((unsigned long)(ms))
struct completion { bool done; };
struct apple_dcp {
    struct mutex tb_lock;
    void *tb_dpin_ctx;
    struct { struct completion linkcfg_completion; } dptxport[1];
};
'''

SHAPES = r'''
/* Only the infrastructure fields used by the extracted production queries. */
struct apple_dpin_ctx {
    struct mutex lock;
    struct apple_dpin_state state;
    struct apple_dpin_tokens tokens;
};
struct apple_dpin_binding { struct apple_dpin_ctx *ctx; u64 generation; };
static struct apple_dpin_ctx ctx;
static struct apple_dpin_binding binding;
static struct apple_dcp dcp;
static void advance(unsigned long delay)
{
    jiffies += delay;
    if (withdraw_after != ULONG_MAX && jiffies - epoch >= withdraw_after) {
        if (withdrawal == 1)
            assert(apple_dpin_token_request(&ctx.tokens, binding.generation, false));
        else if (withdrawal == 2)
            apple_dpin_token_revoke(&ctx.tokens, binding.generation);
        else if (withdrawal == 3)
            ctx.state.alive = false;
        else if (withdrawal == 4) {
            assert(apple_dpin_token_request(&ctx.tokens, binding.generation + 1, true));
            assert(apple_dpin_token_admit(&ctx.tokens, binding.generation + 1));
        }
        withdraw_after = ULONG_MAX;
    }
}
static unsigned long wait_for_completion_timeout(struct completion *completion,
                                                  unsigned long timeout)
{
    unsigned long elapsed = jiffies - epoch, used;
    assert(!depth && !refs); /* No locks or provider references across sleep. */
    ++waits;
    if (timeout > largest_wait)
        largest_wait = timeout;
    if (completion->done)
        return timeout ? timeout : 1;
    if (finish_after != ULONG_MAX && finish_after - elapsed <= timeout) {
        used = finish_after - elapsed;
        advance(used);
        completion->done = true;
        return timeout > used ? timeout - used : 1;
    }
    advance(timeout);
    return 0;
}
static void reset(bool attached, unsigned long now)
{
    assert(!depth && !refs);
    memset(&ctx, 0, sizeof(ctx));
    memset(&dcp, 0, sizeof(dcp));
    ctx.lock.rank = 2;
    ctx.state.alive = true;
    binding.ctx = &ctx;
    binding.generation = 7;
    assert(apple_dpin_token_request(&ctx.tokens, 7, true));
    assert(apple_dpin_token_admit(&ctx.tokens, 7));
    dcp.tb_lock.rank = 1;
    dcp.tb_dpin_ctx = attached ? &binding : 0;
    epoch = jiffies = now;
    finish_after = withdraw_after = ULONG_MAX;
    waits = symbol_gets = symbol_puts = largest_wait = 0;
    withdrawal = 0;
    provider_available = true;
}
'''

CASES = r'''
int main(void)
{
    bool withdrawn = true;
    unsigned long result;

    /* Direct DP-alt has no provider binding: one unchanged full wait. */
    reset(false, 0);
    result = dcp_dptx_wait_linkcfg(&dcp, 0, 2000, true, &withdrawn);
    assert(!result && !withdrawn && waits == 1 && largest_wait == 2000 && !symbol_gets);

    /* Compact/late-HPD callers opt out even if a binding exists. */
    reset(true, 0);
    withdraw_after = 10; withdrawal = 1;
    result = dcp_dptx_wait_linkcfg(&dcp, 0, 2000, false, &withdrawn);
    assert(!result && !withdrawn && waits == 1 && largest_wait == 2000 && !symbol_gets);

    /* Real token withdrawal, revocation, adapter death and replacement all
     * end within one 50-ms slice, before the ordered detach can run. */
    for (int reason = 1; reason <= 4; ++reason) {
        reset(true, 0);
        withdraw_after = 162; withdrawal = reason;
        result = dcp_dptx_wait_linkcfg(&dcp, 0, 2000, true, &withdrawn);
        assert(!result && withdrawn && jiffies == 200 && waits == 4);
        assert(largest_wait == 50 && symbol_gets == 4 && symbol_puts == symbol_gets && !depth && !refs);
    }

    /* Requested is insufficient without admission; stale cookies are dead. */
    reset(true, 0);
    assert(apple_dp_tunnel_binding_live(&binding));
    apple_dpin_token_revoke(&ctx.tokens, 7);
    assert(!apple_dp_tunnel_binding_live(&binding));
    assert(apple_dpin_token_admit(&ctx.tokens, 7));
    binding.generation = 6;
    assert(!apple_dp_tunnel_binding_live(&binding));
    binding.generation = 0;
    assert(!apple_dp_tunnel_binding_live(&binding));

    /* Healthy tunnels preserve completion's positive remaining-time result. */
    reset(true, 0);
    finish_after = 160;
    result = dcp_dptx_wait_linkcfg(&dcp, 0, 2000, true, &withdrawn);
    assert(result == 1840 && !withdrawn && waits == 4 && symbol_gets == 3 && symbol_puts == symbol_gets);

    /* Completion at the deadline is success (one), not a false timeout. */
    reset(true, 0);
    finish_after = 125;
    result = dcp_dptx_wait_linkcfg(&dcp, 0, 125, true, &withdrawn);
    assert(result == 1 && !withdrawn && waits == 3 && jiffies == 125);

    /* Deadline arithmetic survives unsigned jiffies rollover. */
    reset(true, ULONG_MAX - 30);
    finish_after = 70;
    result = dcp_dptx_wait_linkcfg(&dcp, 0, 125, true, &withdrawn);
    assert(result == 55 && !withdrawn && jiffies - epoch == 70);

    /* Short/zero timeouts do not extend the caller's wait. */
    reset(true, 0);
    result = dcp_dptx_wait_linkcfg(&dcp, 0, 25, true, &withdrawn);
    assert(!result && !withdrawn && waits == 1 && jiffies == 25 && largest_wait == 25);
    reset(true, 0);
    result = dcp_dptx_wait_linkcfg(&dcp, 0, 0, true, &withdrawn);
    assert(!result && !withdrawn && !waits && !symbol_gets);

    /* Optional symbol unavailable: preserve legacy timeout, never claim gone. */
    reset(true, 0);
    provider_available = false;
    result = dcp_dptx_wait_linkcfg(&dcp, 0, 125, true, &withdrawn);
    assert(!result && !withdrawn && waits == 3 && symbol_gets == 3 && !symbol_puts && !refs && !depth);
    return 0;
}
'''


class PeripheralProductionTests(unittest.TestCase):
    def test_dead_tunnel_wait_and_binding_lifetime(self):
        sources = production_sources()
        state = sources["drivers/thunderbolt/apple-dpin-state.c"]
        header = sources["drivers/thunderbolt/apple-dpin-state.h"]
        dcp_source = sources["drivers/gpu/drm/apple/dcp.c"]
        records = [record(header, "enum", "apple_dpin_phase"),
                   record(header, "struct", "apple_dpin_state"),
                   record(header, "struct", "apple_dpin_tokens")]
        token_functions = [function(state, name) for name in
                           ("apple_dpin_token_request", "apple_dpin_token_admit",
                            "apple_dpin_token_revoke", "apple_dpin_token_access")]
        poll_macro = re.search(r"^#define DCP_LINKCFG_POLL_MS\s+\d+$", dcp_source, re.MULTILINE)
        if not poll_macro:
            self.fail("missing production link wait interval")
        production = [function(sources["drivers/thunderbolt/apple.c"], "apple_dp_tunnel_binding_live"),
                      function(dcp_source, "dcp_tb_tunnel_withdrawn"),
                      function(dcp_source, "dcp_dptx_wait_linkcfg")]
        harness = "\n".join([PRELUDE, *records, *token_functions, SHAPES,
                             poll_macro[0], *production, CASES])
        with tempfile.TemporaryDirectory(prefix="j716s-peripheral-host-") as temporary:
            directory = Path(temporary)
            path = directory / "peripherals.c"
            path.write_text(harness)
            executable = directory / "peripherals"
            subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-O2",
                            str(path), "-o", str(executable)], check=True)
            subprocess.run([str(executable)], check=True, timeout=10)


if __name__ == "__main__":
    unittest.main()
