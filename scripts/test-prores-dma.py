#!/usr/bin/env python3
"""Host boundary tests for the patched production ProRes ownership paths.

Compiles production IRQ/watchdog/publication/PM and mem2mem cancellation bodies
against a synthetic MMIO/vb2 boundary. No devices, firmware, boot changes or
silicon qualification. Negative controls must fail the same assertions.
Run against a kernel tree with kernel-j716s-prores-dma-ownership.patch applied.
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
    match = re.search(r"^(?:static )?[A-Za-z_][^\n]*\b" + re.escape(name) +
                      r"\([^;]*?\)\n\{", source, re.M)
    if not match:
        raise ValueError("missing production function: " + name)
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end]


def replaced(source, old, new):
    if source.count(old) != 1:
        raise ValueError("negative control no longer matches exactly")
    return source.replace(old, new)


def harness(kernel, mutation=None):
    directory = kernel / "drivers/media/platform/apple/prores"
    source = (directory / "apple-prores-drv.c").read_text()
    header = (directory / "apple-prores.h").read_text()
    core = (kernel / "drivers/media/v4l2-core/v4l2-mem2mem.c").read_text()
    if ".suppress_bind_attrs = true" not in source or "reset_control_reset(" in source:
        raise ValueError("source does not preserve the fixed APR/DART quarantine")
    names = ("apr_quarantine", "apr_take_done", "apr_irq", "apr_watchdog",
             "apr_device_run", "apr_runtime_suspend", "apr_suspend", "apr_resume")
    bodies = {name: function(source, name) for name in names}
    if mutation == "timeout-release":
        bodies["apr_watchdog"] = replaced(bodies["apr_watchdog"], "apr_quarantine(apr);",
            "apr->active = NULL; apr_finish_job(apr, ctx, -ETIMEDOUT);")
    elif mutation == "error-release":
        bodies["apr_take_done"] = replaced(bodies["apr_take_done"], "apr_quarantine(apr);",
            "apr->active = NULL; apr_finish_job(apr, ctx, -EIO);")
    elif mutation == "late-done":
        bodies["apr_take_done"] = replaced(bodies["apr_take_done"],
            "if (!ctx || apr->broken)", "if (!ctx)")
    elif mutation == "no-module-pin":
        bodies["apr_quarantine"] = replaced(bodies["apr_quarantine"],
            "__module_get(THIS_MODULE);", "(void)THIS_MODULE;")
    elif mutation == "no-device-pin":
        bodies["apr_quarantine"] = replaced(bodies["apr_quarantine"],
            "get_device(apr->dev);", "(void)apr->dev;")
    elif mutation == "ack-order":
        bodies["apr_take_done"] = replaced(bodies["apr_take_done"],
            "apr_write(apr, APR_REG_IRQ_STATUS, status);", "(void)status;")
    elif mutation == "sleep-active":
        bodies["apr_suspend"] = replaced(bodies["apr_suspend"],
            "if (apr->active)", "if (false)")
    elif mutation == "sleep-publication":
        bodies["apr_device_run"] = replaced(bodies["apr_device_run"],
            "if (apr->broken || apr->suspending)", "if (apr->broken)")
    defines = "\n".join(line for line in header.splitlines() if line.startswith("#define APR_")
                         and any(word in line for word in ("APR_REG_", "APR_IRQ_", "APR_MODE_",
                                                           "APR_RING_PTR_MASK")))
    defines += "\n" + "\n".join(line for line in source.splitlines() if
                                line.startswith(("#define APR_TIMEOUT_MS", "#define APR_POLL_MS")))
    return BOUNDARY + "\n" + defines + "\n" + ADAPTER + "\n" + "\n".join(bodies.values()) + "\n" + \
        function(core, "v4l2_m2m_cancel_job") + "\n" + function(core, "v4l2_m2m_ctx_release") + TESTS


BOUNDARY = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <errno.h>
#include <setjmp.h>
#include <stdio.h>
#include <string.h>
typedef uint32_t u32;
typedef int irqreturn_t;
#define BIT(n) (1U << (n))
#define GENMASK(h,l) ((~0U << (l)) & (~0U >> (31 - (h))))
#define IRQ_NONE 0
#define IRQ_HANDLED 1
#define THIS_MODULE NULL
#define TRANS_QUEUED BIT(0)
#define TRANS_RUNNING BIT(1)
#define TRANS_ABORT BIT(2)
#define spin_lock(p) ((void)(p))
#define spin_unlock(p) ((void)(p))
#define spin_lock_irqsave(p,f) do { (void)(p); (f) = 0; } while (0)
#define spin_unlock_irqrestore(p,f) do { (void)(p); (void)(f); } while (0)
#define dev_err(...) ((void)0)
#define dev_err_ratelimited(...) ((void)0)
#define dev_warn_once(...) ((void)0)
#define dprintk(...) ((void)0)
#define container_of(p,t,m) ((p)->owner)
#define msecs_to_jiffies(n) (n)
#define time_before(a,b) ((long)((a)-(b)) < 0)
#define dma_wmb() (barriers++)
#define EXPORT_SYMBOL_GPL(s)
struct vb2_queue { bool error; };
struct vb2_buffer { unsigned char output[8]; };
struct vb2_v4l2_buffer { struct vb2_buffer vb2_buf; };
struct v4l2_m2m_ctx;
struct v4l2_m2m_ops { void (*job_abort)(void *); };
struct v4l2_m2m_dev { int job_spinlock; const struct v4l2_m2m_ops *m2m_ops; };
struct v4l2_m2m_ctx {
    struct vb2_queue src, dst;
    struct { struct vb2_queue q; } cap_q_ctx, out_q_ctx;
    struct v4l2_m2m_dev *m2m_dev;
    unsigned int job_flags;
    int queue, finished;
    void *priv;
};
struct apr_dev;
struct device { struct apr_dev *driver; };
struct work_struct { struct apr_dev *owner; };
struct delayed_work { struct work_struct work; };
struct apr_frame { int placeholder; };
struct apr_variant { u32 desc_bytes, ring_bytes; int (*build_desc)(struct apr_frame *, void *); };
struct apr_ctx { struct apr_dev *apr; struct { struct v4l2_m2m_ctx *m2m_ctx; } fh; };
struct apr_dev {
    struct apr_ctx *active;
    struct device *dev;
    struct v4l2_m2m_dev *m2m_dev;
    const struct apr_variant *variant;
    bool broken, suspending, ring_ready;
    int hw_lock;
    unsigned long started;
    u32 head;
    unsigned char *regs, *ring;
    struct delayed_work watchdog;
};
static u32 mmio[0x2000 / 4], pending_ack;
static unsigned int releases, pm_refs, module_pins, device_pins, barriers, publications;
static unsigned int force_suspends, scheduler_pauses, scheduler_resumes, queue_releases;
static unsigned int reschedules, cancel_waits, resets;
static int suspend_rc, resume_rc, last_error;
static unsigned long jiffies;
static bool race_publication;
static jmp_buf cancellation;
#define wait_event(wq,condition) do { (void)(wq); if (!(condition)) { cancel_waits++; longjmp(cancellation, 1); } } while (0)
'''

ADAPTER = r'''
static struct vb2_v4l2_buffer src_buffer, dst_buffer;
static struct v4l2_m2m_ops core_ops;
static struct v4l2_m2m_dev core_dev = { .m2m_ops = &core_ops };
static struct v4l2_m2m_ctx m2m = { .m2m_dev = &core_dev };
static struct apr_ctx owner = { .fh = { .m2m_ctx = &m2m } };
static unsigned char ring[0x20000];
static u32 apr_read(struct apr_dev *a, u32 reg) {
    (void)a;
    if (reg != APR_REG_IRQ_STATUS) { mmio[APR_REG_IRQ_STATUS/4] &= ~pending_ack; pending_ack = 0; }
    return mmio[reg/4];
}
static void apr_write(struct apr_dev *a, u32 reg, u32 value) {
    (void)a;
    if (reg == APR_REG_IRQ_STATUS) pending_ack |= value;
    else mmio[reg/4] = value;
}
static void __module_get(void *m) { (void)m; module_pins++; }
static void get_device(struct device *d) { assert(d); device_pins++; }
static struct vb2_queue *v4l2_m2m_get_src_vq(struct v4l2_m2m_ctx *m) { return &m->src; }
static struct vb2_queue *v4l2_m2m_get_dst_vq(struct v4l2_m2m_ctx *m) { return &m->dst; }
static void vb2_queue_error(struct vb2_queue *q) { q->error = true; }
static void cancel_delayed_work(void *w) { (void)w; }
static void schedule_delayed_work(void *w, int d) { (void)w; (void)d; reschedules++; }
static void apr_finish_job(struct apr_dev *a, struct apr_ctx *ctx, int err) {
    assert(ctx == &owner && pm_refs); (void)a;
    assert(!pending_ack && !mmio[APR_REG_IRQ_STATUS/4]);
    releases++; pm_refs--; m2m.job_flags &= ~TRANS_RUNNING; last_error = err;
}
static void *dev_get_drvdata(struct device *d) { return d->driver; }
static struct vb2_v4l2_buffer *v4l2_m2m_next_src_buf(struct v4l2_m2m_ctx *m) { (void)m; return &src_buffer; }
static struct vb2_v4l2_buffer *v4l2_m2m_next_dst_buf(struct v4l2_m2m_ctx *m) { (void)m; return &dst_buffer; }
static int pm_runtime_resume_and_get(struct device *d) { (void)d; pm_refs++; return 0; }
static void pm_runtime_get_noresume(struct device *d) { (void)d; pm_refs++; }
static void apr_fill_frame(struct apr_ctx *c, void *s, void *d, struct apr_frame *f) {
    (void)c; (void)s; (void)d; memset(f, 0, sizeof(*f));
}
static void *vb2_plane_vaddr(struct vb2_buffer *b, int p) { (void)p; return b->output; }
static void apr_hw_init(struct apr_dev *a) { assert(!a->active); a->ring_ready = true; a->head = 0; }
static int build_desc(struct apr_frame *f, void *d) { (void)f; memset(d, 0, 0x180); return 0; }
static const struct apr_variant variant = { .desc_bytes = 0x180, .ring_bytes = 0x20000, .build_desc = build_desc };
static void writel(u32 v, void *p) { assert(barriers); publications++; *(u32 *)p = v; }
static void apr_device_run(void *priv);
static void v4l2_m2m_suspend(struct v4l2_m2m_dev *m) {
    (void)m; scheduler_pauses++;
    if (race_publication) apr_device_run(&owner);
}
static void v4l2_m2m_resume(struct v4l2_m2m_dev *m) { (void)m; scheduler_resumes++; }
static int pm_runtime_force_suspend(struct device *d) {
    force_suspends++; assert(!d->driver->active); return suspend_rc;
}
static int pm_runtime_force_resume(struct device *d) { (void)d; return resume_rc; }
static void list_del(void *p) { (void)p; }
static void vb2_queue_release(struct vb2_queue *q) { (void)q; queue_releases++; }
static void kfree(void *p) { (void)p; queue_releases++; }
'''

TESTS = r'''
static struct device device;
static struct apr_dev begin(bool active) {
    struct apr_dev a = { .active = active ? &owner : NULL, .dev = &device,
        .m2m_dev = &core_dev, .variant = &variant, .ring_ready = true,
        .head = 0x180, .regs = (unsigned char *)mmio, .ring = ring };
    memset(mmio, 0, sizeof(mmio)); pending_ack = 0;
    releases = module_pins = device_pins = barriers = publications = 0;
    force_suspends = scheduler_pauses = scheduler_resumes = queue_releases = 0;
    cancel_waits = reschedules = resets = 0;
    pm_refs = active ? 1 : 0; jiffies = 100;
    suspend_rc = resume_rc = last_error = 0; race_publication = false;
    m2m.job_flags = active ? TRANS_RUNNING : 0; m2m.src.error = m2m.dst.error = false;
    return a;
}
static void bind(struct apr_dev *a) { owner.apr = a; device.driver = a; a->watchdog.work.owner = a; }
static void retained(struct apr_dev *a) {
    assert(a->broken && a->active == &owner && !a->ring_ready);
    assert(!releases && pm_refs == 1 && (m2m.job_flags & TRANS_RUNNING));
    assert(module_pins == 1 && device_pins == 1);
    assert(m2m.src.error && m2m.dst.error && !pending_ack && !resets);
    assert(mmio[APR_REG_MODE/4] == APR_MODE_OFF && !mmio[APR_REG_IRQ_ENABLE/4]);
}
static void late_and_cancel(struct apr_dev *a) {
    mmio[APR_REG_IRQ_STATUS/4] = APR_IRQ_DONE; mmio[APR_REG_RING_TAIL/4] = a->head;
    for (unsigned int i = 0; i < 8; i++) {
        assert(apr_irq(0, a) == IRQ_NONE); apr_watchdog(&a->watchdog.work); retained(a);
    }
    if (!setjmp(cancellation)) { v4l2_m2m_ctx_release(&m2m); assert(false); }
    assert(cancel_waits == 1 && !queue_releases); retained(a);
    assert(apr_suspend(a->dev) == -EBUSY && !scheduler_pauses && !force_suspends);
    assert(apr_runtime_suspend(a->dev) == -EBUSY); retained(a);
}
int main(void) {
    /* Fetch-ahead without DONE is not a completion. */
    {
        struct apr_dev a = begin(true); bind(&a); mmio[APR_REG_RING_TAIL/4] = a.head;
        for (unsigned int i = 0; i < 8; i++) {
            assert(apr_irq(0, &a) == IRQ_NONE); apr_watchdog(&a.watchdog.work);
            assert(a.active == &owner && !releases && pm_refs == 1 && !a.broken);
        }
        mmio[APR_REG_IRQ_STATUS/4] = APR_IRQ_DONE;
        assert(apr_irq(0, &a) == IRQ_HANDLED);
        assert(!a.active && releases == 1 && !pm_refs && !pending_ack);
        assert(!module_pins && !device_pins && !a.broken);
        apr_watchdog(&a.watchdog.work); assert(apr_irq(0, &a) == IRQ_NONE && releases == 1);
    }
    /* A polled exact DONE also finishes once. */
    {
        struct apr_dev a = begin(true); bind(&a);
        mmio[APR_REG_RING_TAIL/4] = a.head; mmio[APR_REG_IRQ_STATUS/4] = APR_IRQ_DONE;
        apr_watchdog(&a.watchdog.work); assert(releases == 1 && !pm_refs && !pending_ack);
    }
    /* Timeout preserves every ownership boundary; no reset/retry. */
    {
        struct apr_dev a = begin(true); bind(&a); jiffies = 2000;
        apr_watchdog(&a.watchdog.work); retained(&a); late_and_cancel(&a);
    }
    /* ERROR, DONE|ERROR, unknown status, malformed or early tail quarantine. */
    for (unsigned int fault = 0; fault < 6; fault++) {
        struct apr_dev a = begin(true); bind(&a);
        mmio[APR_REG_IRQ_STATUS/4] = fault == 0 ? APR_IRQ_ERROR :
            fault == 1 ? APR_IRQ_DONE | APR_IRQ_ERROR : fault == 2 ? BIT(7) : APR_IRQ_DONE;
        mmio[APR_REG_RING_TAIL/4] = a.head + (fault == 3 ? 4 : fault == 4 ? -0x180 : fault == 5 ? 0x180 : 0);
        assert(apr_irq(0, &a) == IRQ_HANDLED); retained(&a); late_and_cancel(&a);
    }
    /* Normal publication orders the descriptor before one head advance. */
    {
        struct apr_dev a = begin(false); bind(&a); a.head = 0; apr_device_run(&owner);
        assert(a.active == &owner && publications == 1 && barriers == 1 && pm_refs == 1);
        assert(a.head == 0x180 && mmio[APR_REG_RING_HEAD/4] == 0x180 && !releases);
    }
    /* A run entering while system sleep pauses m2m cannot publish. */
    for (unsigned int failure = 0; failure < 2; failure++) {
        struct apr_dev a = begin(false); bind(&a); race_publication = true;
        suspend_rc = failure ? -EIO : 0;
        assert(apr_suspend(a.dev) == suspend_rc);
        assert(!publications && !a.active && !pm_refs && last_error == -EBUSY);
        assert(force_suspends == 1 && scheduler_pauses == 1);
        if (failure) assert(!a.suspending && scheduler_resumes == 1);
        else {
            assert(a.suspending); resume_rc = -EIO; assert(apr_resume(a.dev) == -EIO && a.suspending);
            resume_rc = 0; assert(!apr_resume(a.dev) && !a.suspending && scheduler_resumes == 1);
        }
    }
    /* Without an active owner the actual core release can drop its queues. */
    {
        struct apr_dev a = begin(false); bind(&a); v4l2_m2m_ctx_release(&m2m);
        assert(queue_releases == 3 && !cancel_waits);
    }
    puts("PASS: depth-one ownership, exact DONE, quarantine, late IRQ, cancellation and sleep/publication boundary");
    return 0;
}
'''


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel-source", type=Path, default=PROJECT / "work/sources/kernel")
    args = parser.parse_args()
    mutations = ("timeout-release", "error-release", "late-done", "no-module-pin",
                 "no-device-pin", "ack-order", "sleep-active", "sleep-publication")
    with tempfile.TemporaryDirectory(prefix="j716s-prores-dma-") as temporary:
        root = Path(temporary)
        for mutation in (None,) + mutations:
            source, exe = root / "ownership.c", root / "ownership"
            source.write_text(harness(args.kernel_source, mutation))
            subprocess.run([os.environ.get("CC", "cc"), "-std=c11", "-Wall", "-Wextra", "-Werror",
                            "-Wno-unused-parameter", "-Wno-unused-function", "-fsanitize=address,undefined",
                            str(source), "-o", str(exe)], check=True)
            result = subprocess.run([str(exe)], capture_output=True, text=True, check=False)
            if mutation is None:
                if result.returncode:
                    raise SystemExit(result.stdout + result.stderr)
                print(result.stdout, end="")
            elif result.returncode == 0:
                raise SystemExit("negative control survived: " + mutation)
            elif "Assertion" not in result.stderr and "assertion" not in result.stderr:
                raise SystemExit("negative control failed outside assertion: " + mutation + "\n" + result.stderr)
            else:
                print("PASS: assertion rejected " + mutation)


if __name__ == "__main__":
    main()
