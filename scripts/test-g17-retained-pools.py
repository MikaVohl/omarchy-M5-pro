#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only OR MIT
"""Run patched G17 retained-pool production bodies against host boundaries.

Compiles the housekeeping loop, exact-owner release proof, free-list lease
state machine, backing vacate, physical-ID allocator, park and owner-rebind
bodies. Synthetic mappings, objects and control-ring memory replace hardware;
no kernel source is changed and no devices or firmware are accessed. Tests
require kernel-g17-retained-pool-pages.patch in the supplied disposable tree.
"""

import argparse
import os
from pathlib import Path
import re
import resource
import subprocess
import tempfile


def item(source, signature):
    if source.count(signature) != 1:
        raise ValueError("missing or ambiguous production item: " + signature)
    start = source.index(signature)
    begin = source.index("{", start)
    depth, end = 1, begin + 1
    while depth:
        if end >= len(source):
            raise ValueError("unterminated production item: " + signature)
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    line = source.rfind("\n", 0, start) + 1
    while line:
        previous = source.rfind("\n", 0, line - 1) + 1
        if not source[previous:line].strip().startswith("#["):
            break
        start = line = previous
    return source[start:end]


def public(source):
    # Visibility is the sole adjustment to extracted production items.
    return re.sub(r"\bpub\([^)]*\)\s+", "pub ", source)


def replaced(source, old, new):
    if source.count(old) != 1:
        raise ValueError("negative control no longer matches exactly: " + old)
    return source.replace(old, new)


def constant(source, name):
    matches = re.findall(r"^(?:pub\([^)]*\)\s+)?const " + re.escape(name) +
                         r":[^\n]+;$", source, re.M)
    if len(matches) != 1:
        raise ValueError("missing or ambiguous production constant: " + name)
    return public(matches[0])


def harness(kernel, mutation=None):
    root = kernel / "drivers/gpu/drm/asahi/g17"
    render = (root / "runtime/render.rs").read_text()
    pools = (root / "runtime/render/pools.rs").read_text()
    freelist = (root / "freelist.rs").read_text()
    queue = (root / "queue/render.rs").read_text()
    parked = (root / "queue/render/parked.rs").read_text()
    runtime = (root / "runtime.rs").read_text()
    ids = (root / "freelist/render_ids.rs").read_text().split("#[cfg(test)]", 1)[0]

    loop = item(render, "pub(in crate::g17) fn vacate_evicted_render_pools(")
    proof = item(pools, "pub(super) fn backing_released(")
    park = item(parked, "pub(crate) fn park(self)")
    rebind = item(queue, "pub(crate) fn bind_owner(")
    if mutation == "omit-kept-graphs":
        loop = replaced(loop, "for graph in render.kept_graphs.iter()",
                        "for graph in render.kept_graphs.iter().take(0)")
    elif mutation == "ignore-final-release":
        proof = replaced(proof, "&& lease.released()", "&& true")
    elif mutation == "ignore-owner-epoch":
        proof = replaced(proof, "lease.owner() == Self::owner(pool)", "true")
    elif mutation == "accept-missing-proof":
        proof = replaced(proof, ".is_some_and(|lease| {", ".is_none_or(|lease| {")
    elif mutation == "drop-parked-pin":
        park = replaced(park, "_pool_pin: self.pool_pin,", "_pool_pin: None,")
    elif mutation == "drop-replaced-pin":
        rebind = replaced(rebind,
            "let old_pool_pin = core::mem::replace(&mut self.pool_pin, binding.pool_pin.take());",
            "self.pool_pin = binding.pool_pin.take(); let old_pool_pin = None;")
    elif mutation == "ignore-deferred-room":
        loop = replaced(loop,
            "for graph in render.kept_graphs.iter() {\n            if !deferred.render_backing_room() {",
            "for graph in render.kept_graphs.iter() {\n            if false {")
    elif mutation is not None:
        raise ValueError("unknown mutation: " + mutation)

    pool_impl = item(freelist, "impl RenderPool {")
    retained_impl = item(queue, "impl RetiredBinding {")
    declarations = "\n".join(item(source, signature) for source, signature in (
        (freelist, "pub(crate) struct Owner {"),
        (freelist, "enum LeasePhase {"),
        (freelist, "pub(crate) struct Lease {"),
        (pools, "pub(super) struct Pools {"),
        (queue, "pub(crate) struct RetiredBinding {"),
        (queue, "pub(crate) struct PreparedBinding {"),
        (parked, "pub(crate) struct ParkedPair {"),
    ))
    pool_methods = "\n".join(item(pool_impl, signature) for signature in (
        "pub(crate) fn vacant(", "pub(crate) fn client_evicted(",
        "pub(crate) fn vacate(", "pub(crate) fn serial(", "pub(crate) fn epoch(",
        "pub(crate) fn id(", "pub(crate) fn control_va(",
        "pub(crate) fn page_list_va(", "pub(crate) fn run_list_va(",
        "pub(crate) fn end_va(",
    ))
    pool_registry_methods = "\n".join(item(pools, signature) for signature in (
        "fn slot(", "pub(super) fn owner(", "pub(super) fn idle(",
        "pub(super) fn observe(",
    ))
    deferred_methods = "\n".join(item(runtime, signature) for signature in (
        "pub(super) fn render_backing_room(", "pub(super) fn defer_render_backing(",
    ))
    constants = constant(pools, "POOLS") + "\n" + constant(pools, "FIRST_RENDER_POOL")
    boundary = BOUNDARY.replace("PAGE_LIST_SIZE_PRODUCTION",
        constant((root / "fw/freelist.rs").read_text(), "PAGE_LIST_SIZE"))
    boundary = boundary.replace("CONTROL_RING_PRODUCTION",
        constant((root / "fw/channels.rs").read_text(), "DEVICE_CONTROL_SLOTS"))
    boundary = boundary.replace("RUN_SLOT_PRODUCTION",
        public(item((root / "fw/initdata.rs").read_text(), "pub(crate) struct FreeListRunSlot {")))
    return boundary + "\nmod render_ids {\n" + public(ids) + "\n}\n" + \
        public(declarations) + "\n" + constants + "\nmod production {\nuse super::*;\n" + \
        public(item(freelist, "impl Lease {")) + "\n" + \
        public(item(freelist, "pub(crate) fn control_consumed(")) + \
        "\nimpl RenderPool {\n" + public(pool_methods) + "\n}\nimpl Pools {\n" + \
        public(pool_registry_methods + "\n" + proof) + "\n}\nimpl Registry {\n" + \
        public(loop) + "\n}\nimpl RetiredBinding {\n" + \
        public(item(retained_impl, "pub(crate) fn pool(")) + "\n}\nimpl Pair {\n" + \
        public(park + "\n" + rebind) + "\n}\nimpl DeferredBatch {\n" + \
        public(deferred_methods) + "\n}\n}\n" + TESTS


BOUNDARY = r'''
#![allow(dead_code)]
use std::sync::{Arc, Mutex as StdMutex, MutexGuard,
    atomic::{fence, AtomicBool, AtomicU32, AtomicU64, AtomicUsize, Ordering}};
use std::marker::PhantomData;
use std::mem::size_of;
type Result<T=()> = std::result::Result<T, i32>;
const EINVAL: i32 = -22;
const EIO: i32 = -5;
const EBUSY: i32 = -16;
const EOVERFLOW: i32 = -75;
PAGE_LIST_SIZE_PRODUCTION
static DEVICE_LOCKED: AtomicBool = AtomicBool::new(false);
struct DeviceLock;
impl Drop for DeviceLock {
    fn drop(&mut self) { DEVICE_LOCKED.store(false, Ordering::SeqCst); }
}
fn now_ns() -> u64 { 1 }
type KVec<T> = Vec<T>;
type KBox<T> = Box<T>;
struct Mutex<T>(StdMutex<T>);
impl<T> Mutex<T> {
    fn new(value: T) -> Self { Self(StdMutex::new(value)) }
    fn lock(&self) -> MutexGuard<'_, T> { self.0.lock().unwrap() }
}
struct KernelObject(u64);
impl KernelObject { fn gpu_va(&self) -> u64 { self.0 } }
struct Allocator<'a>(PhantomData<&'a ()>);
mod mmu {
    pub struct KernelMapping;
    pub struct Vm(pub u64);
    impl Vm { pub fn id(&self) -> u64 { self.0 } }
    pub struct ResidencyGate(pub bool);
    impl ResidencyGate { pub fn evicted(&self) -> bool { self.0 } }
}
mod g17 {
    pub mod freelist {
        pub type PoolPin = crate::PoolPin;
        pub type RenderBacking = crate::RenderBacking;
    }
    pub mod context { pub type SchedulerStorage = (); }
    pub mod object { pub type KernelObject = crate::KernelObject; }
    pub mod initdata {
        use super::super::*;
        pub struct InitData {
            pub consumer: AtomicU32, pub producer: AtomicU32, pub failed: bool,
        }
        impl InitData {
            pub fn control_consumer(&self) -> Result<&AtomicU32> {
                if self.failed { Err(EIO) } else { Ok(&self.consumer) }
            }
            pub fn control_producer(&self) -> Result<&AtomicU32> {
                if self.failed { Err(EIO) } else { Ok(&self.producer) }
            }
        }
    }
    pub mod fw {
        pub struct Variant;
        pub mod render { pub type Deflake = (); }
        pub mod channels {
            CONTROL_RING_PRODUCTION
            pub struct FreeListCompletion;
            pub struct FreeListRelease;
            impl FreeListRelease { pub fn new(_: u64, _: u32) -> Self { Self } }
        }
        pub mod initdata {
            RUN_SLOT_PRODUCTION
        }
        pub mod m5 {
            pub mod initdata { pub struct Board; }
            pub mod pool {
                #[derive(Clone, Copy)]
                pub struct PoolGeometry { pub directory_bytes: usize, pub blocks: usize }
            }
        }
    }
}
use g17::{fw, freelist, initdata::InitData};
static VARIANT: fw::Variant = fw::Variant;
mod kick {
    #[derive(Clone, Copy)]
    pub struct Id(pub u8);
    pub struct Queue(pub Id);
    impl Queue { pub fn id(&self) -> Id { self.0 } }
}
struct Tracker;
struct Context {
    vm: mmu::Vm, status: Arc<()>, scheduler: Arc<()>, current: bool,
}
impl Context {
    fn vm(&self) -> &mmu::Vm { &self.vm }
    fn status(&self) -> &Arc<()> { &self.status }
    fn scheduler_owner(&self) -> Arc<()> { self.scheduler.clone() }
    fn is_current(&self) -> bool { self.current }
}
struct Graph;
impl Graph { fn is_m5(&self) -> bool { true } }
struct Memory { context: Arc<Context>, graph: Graph, live: Arc<AtomicUsize> }
impl Memory {
    fn set_owner(&mut self, _: [u8; 2], _: &Arc<Context>, _: bool) -> Result { Ok(()) }
    fn take_pool_mappings(&mut self) -> Option<(u8, KVec<mmu::KernelMapping>)> { None }
    fn put_pool_mappings(&mut self, _: u8, _: KVec<mmu::KernelMapping>) -> Result { Ok(()) }
}
impl Drop for Memory {
    fn drop(&mut self) { self.live.fetch_sub(1, Ordering::SeqCst); }
}
mod memory { pub type Memory = crate::Memory; }
fn install_memory(_: &Allocator<'_>, _: &KernelObject, _: u8, _: &mut Memory,
    _: &mut Option<KVec<mmu::KernelMapping>>,
    _: &mut Option<[Option<mmu::KernelMapping>; 2]>) -> Result { Ok(()) }
struct PoolPin { ids: Arc<Mutex<render_ids::State>>, lease: render_ids::Lease }
impl Drop for PoolPin {
    fn drop(&mut self) { self.ids.lock().unpin(self.lease).unwrap(); }
}
struct RenderId(u16);
impl RenderId { fn slot(&self) -> u16 { self.0 } }
#[derive(Clone, Copy)]
enum PageLayout { Neo { blocks: usize }, M5(fw::m5::pool::PoolGeometry) }
pub struct RenderBacking { live: Arc<AtomicUsize> }
impl Drop for RenderBacking {
    fn drop(&mut self) {
        assert!(!DEVICE_LOCKED.load(Ordering::SeqCst), "mapping destruction under device mutex");
        self.live.fetch_sub(1, Ordering::SeqCst);
    }
}
struct RenderStorage { backing: Mutex<Option<RenderBacking>>, page_list: AtomicU64 }
pub struct RenderPool {
    storage: RenderStorage, epoch: AtomicU64, client_residency: Option<Arc<mmu::ResidencyGate>>,
    control: KernelObject, serial: u64, id: RenderId, layout: PageLayout,
}
struct Pair {
    pool_pin: Option<PoolPin>, memory: KBox<Memory>, free_list: Arc<RenderPool>,
    kicks: [kick::Queue; 2], slot: u8, ordinal: u64, clusters: u32,
    deflake: fw::render::Deflake, descriptor_flags: [u32; 2],
    variant: &'static fw::Variant, board: Option<&'static fw::m5::initdata::Board>,
    installed: [bool; 2], recovery_generation: u64, registered: Option<u8>,
    outer_started: u8, previous: Option<RetiredBinding>, displaced: Option<RetiredBinding>,
    executed: u64, owner: Option<u64>, initial_packet: u64, graph_started: bool,
    context_update: bool, identity_update: bool, cursors: [u32; 2], configs: [Option<()>; 2],
}
impl Pair {
    fn context(&self) -> &Arc<Context> { &self.memory.context }
    fn buffer_id(&self) -> u8 { 9 }
    fn qids(&self) -> [u8; 2] { self.kicks.each_ref().map(|queue| queue.id().0) }
    fn bindable(&self, _: u64, _: &Arc<Context>) -> Result<bool> { Ok(true) }
}
struct Entry { constructing: bool, growing: bool, preparing: Option<()>, pool: Option<Arc<RenderPool>> }
struct RenderState { entries: Vec<Option<Entry>>, kept_graphs: Vec<RetiredBinding>, pools: Pools }
struct Registry { render: RenderState }
struct Reserved<T> { values: Vec<T>, capacity: usize }
impl<T> Reserved<T> {
    fn new(capacity: usize) -> Self { Self { values: Vec::with_capacity(capacity), capacity } }
    fn len(&self) -> usize { self.values.len() }
    fn capacity(&self) -> usize { self.capacity }
    fn push_within_capacity(&mut self, value: T) -> std::result::Result<(), T> {
        if self.len() == self.capacity { Err(value) } else { self.values.push(value); Ok(()) }
    }
    fn clear(&mut self) { self.values.clear(); }
}
struct DeferredBatch { render: Reserved<RenderBacking> }
impl DeferredBatch { fn new(capacity: usize) -> Self { Self { render: Reserved::new(capacity) } } }
'''


TESTS = r'''
fn context(vm: u64) -> Arc<Context> {
    Arc::new(Context { vm: mmu::Vm(vm), status: Arc::new(()), scheduler: Arc::new(()), current: true })
}
fn memory(context: Arc<Context>, live: &Arc<AtomicUsize>) -> Box<Memory> {
    live.fetch_add(1, Ordering::SeqCst);
    Box::new(Memory { context, graph: Graph, live: live.clone() })
}
fn pool(ids: &Arc<Mutex<render_ids::State>>, serial: u64, live: &Arc<AtomicUsize>)
    -> (Arc<RenderPool>, PoolPin)
{
    let lease = ids.lock().claim(serial, |_| false).unwrap().lease;
    live.fetch_add(1, Ordering::SeqCst);
    let pool = Arc::new(RenderPool {
        storage: RenderStorage {
            backing: Mutex::new(Some(RenderBacking { live: live.clone() })),
            page_list: AtomicU64::new(0x100000 + serial * 0x10000),
        },
        epoch: AtomicU64::new(3), client_residency: Some(Arc::new(mmu::ResidencyGate(false))),
        control: KernelObject(0x800000 + serial * 0x1000), serial, id: RenderId(lease.slot),
        layout: PageLayout::M5(fw::m5::pool::PoolGeometry { directory_bytes: 0x2000, blocks: 4 }),
    });
    (pool, PoolPin { ids: ids.clone(), lease })
}
fn pair(pool: Arc<RenderPool>, pin: PoolPin, context: Arc<Context>, graphs: &Arc<AtomicUsize>) -> Pair {
    Pair {
        pool_pin: Some(pin), memory: memory(context, graphs), free_list: pool,
        kicks: [kick::Queue(kick::Id(6)), kick::Queue(kick::Id(7))], slot: 0, ordinal: 1,
        clusters: 1, deflake: (), descriptor_flags: [0; 2], variant: &VARIANT, board: None,
        installed: [true; 2], recovery_generation: 1, registered: Some(0), outer_started: 1,
        previous: None, displaced: None, executed: 1, owner: Some(1), initial_packet: 1,
        graph_started: true, context_update: false, identity_update: false,
        cursors: [1; 2], configs: [None; 2],
    }
}
fn registry(graphs: Vec<RetiredBinding>) -> Registry {
    Registry { render: RenderState {
        entries: vec![], kept_graphs: graphs, pools: Pools { leases: vec![None; POOLS] },
    } }
}
fn init(consumer: u32, producer: u32) -> g17::initdata::InitData {
    g17::initdata::InitData {
        consumer: AtomicU32::new(consumer), producer: AtomicU32::new(producer), failed: false,
    }
}
fn sent(owner: Owner, cursor: u32) -> Lease {
    let mut lease = Lease::acquire(None, owner).unwrap();
    lease.retire_prepare().unwrap();
    lease.complete_render(false).unwrap();
    lease.mark_release_sent(cursor).unwrap();
    lease
}
fn housekeeping(registry: &mut Registry, init: &g17::initdata::InitData,
    deferred: &mut DeferredBatch) -> Result
{
    assert!(!DEVICE_LOCKED.swap(true, Ordering::SeqCst));
    let _lock = DeviceLock;
    registry.vacate_evicted_render_pools(init, deferred)
}
fn retained(pool: &RenderPool, deferred: &DeferredBatch) {
    assert!(!pool.vacant(), "unproved kept backing must remain resident");
    assert_eq!(deferred.render.len(), 0, "unproved kept backing must not be deferred");
}

#[test]
fn kept_graph_returns_only_backing_after_consumed_final_release() {
    let ids = Arc::new(Mutex::new(render_ids::State::new()));
    let live = Arc::new(AtomicUsize::new(0));
    let graphs = Arc::new(AtomicUsize::new(0));
    let (pool, pin) = pool(&ids, 1, &live);
    let owner = Pools::owner(&pool);
    let (_, graph) = pair(pool.clone(), pin, context(1), &graphs).park();
    let mut registry = registry(vec![graph]);
    let mut deferred = DeferredBatch::new(4);
    let init = init(0, 3);
    let slot = usize::from(pool.id());
    registry.render.pools.leases[slot] = Some(Lease::acquire(None, owner).unwrap());
    housekeeping(&mut registry, &init, &mut deferred).unwrap();
    retained(&pool, &deferred);
    registry.render.pools.leases[slot].as_mut().unwrap().retire_prepare().unwrap();
    housekeeping(&mut registry, &init, &mut deferred).unwrap();
    retained(&pool, &deferred);
    registry.render.pools.leases[slot].as_mut().unwrap().complete_render(true).unwrap();
    housekeeping(&mut registry, &init, &mut deferred).unwrap();
    retained(&pool, &deferred);
    registry.render.pools.leases[slot].as_mut().unwrap().end_linger();
    housekeeping(&mut registry, &init, &mut deferred).unwrap();
    retained(&pool, &deferred);
    registry.render.pools.leases[slot].as_mut().unwrap().mark_release_sent(3).unwrap();
    housekeeping(&mut registry, &init, &mut deferred).unwrap();
    retained(&pool, &deferred);
    init.consumer.store(3, Ordering::Relaxed);
    housekeeping(&mut registry, &init, &mut deferred).unwrap();
    assert!(pool.vacant(), "a consumed kept-graph release must return backing");
    assert_eq!(deferred.render.len(), 1);
    assert_eq!(live.load(Ordering::SeqCst), 1, "backing remains owned until off-lock finish");
    assert_eq!(graphs.load(Ordering::SeqCst), 1, "firmware-linked graph must stay allocated");
    assert_eq!(registry.render.kept_graphs.len(), 1);
    assert_eq!(pool.id(), owner.buffer_slot as u16);
    assert_eq!(pool.control_va(), owner.control_va);
    assert_eq!(pool.serial(), owner.serial);
    assert_eq!(pool.epoch(), owner.epoch);
    assert_eq!(pool.page_list_va(), 0);
    assert!(ids.lock().reclaim_one(|_| true).is_none(), "kept graph must retain physical identity");
    housekeeping(&mut registry, &init, &mut deferred).unwrap();
    assert_eq!(deferred.render.len(), 1, "vacant graph cannot return backing twice");
    deferred.render.clear();
    assert_eq!(live.load(Ordering::SeqCst), 0);
    assert!(ids.lock().reclaim_one(|_| true).is_none(), "backing release is not graph retirement");
    // Only the modeled processor-stop boundary destroys kept graph ownership.
    registry.render.kept_graphs.clear();
    assert_eq!(graphs.load(Ordering::SeqCst), 0);
    assert!(ids.lock().reclaim_one(|_| true).is_some());
}

#[test]
fn non_m5_fixed_pool_keeps_graph_and_needs_no_rebindable_pin() {
    let ids = Arc::new(Mutex::new(render_ids::State::new()));
    let live = Arc::new(AtomicUsize::new(0));
    let graphs = Arc::new(AtomicUsize::new(0));
    let (mut pool, pin) = pool(&ids, 1, &live);
    Arc::get_mut(&mut pool).unwrap().layout = PageLayout::Neo { blocks: 4 };
    let owner = Pools::owner(&pool);
    let mut pair = pair(pool.clone(), pin, context(1), &graphs);
    // Fixed IDs are retained by the pool object, not an M5 identity pin.
    drop(pair.pool_pin.take());
    let (_, graph) = pair.park();
    assert!(graph._pool_pin.is_none());
    let mut registry = registry(vec![graph]);
    registry.render.pools.leases[usize::from(pool.id())] = Some(sent(owner, 3));
    let init = init(0, 3);
    let mut deferred = DeferredBatch::new(1);
    housekeeping(&mut registry, &init, &mut deferred).unwrap();
    retained(&pool, &deferred);
    init.consumer.store(3, Ordering::Relaxed);
    housekeeping(&mut registry, &init, &mut deferred).unwrap();
    assert!(pool.vacant());
    assert_eq!(deferred.render.len(), 1);
    assert_eq!(graphs.load(Ordering::SeqCst), 1);
    assert_eq!(pool.id(), owner.buffer_slot as u16);
    deferred.render.clear();
    assert_eq!(live.load(Ordering::SeqCst), 0);
}

#[test]
fn stale_owner_epoch_addresses_and_missing_identity_proofs_never_release() {
    for guard in 0..11 {
        let ids = Arc::new(Mutex::new(render_ids::State::new()));
        let live = Arc::new(AtomicUsize::new(0));
        let graphs = Arc::new(AtomicUsize::new(0));
        let (mut pool, pin) = pool(&ids, 1, &live);
        let mut owner = Pools::owner(&pool);
        match guard {
            0 => owner.serial += 1,
            1 => owner.epoch += 1,
            2 => owner.buffer_slot += 1,
            3 => owner.control_va += 8,
            4 => owner.ring_va += 8,
            5 => owner.cookie += 8,
            6 => (), // No lease, despite a populated pool and retained pin.
            7 => Arc::get_mut(&mut pool).unwrap().id.0 = 0,
            8 => Arc::get_mut(&mut pool).unwrap().id.0 = 128,
            9 => Arc::get_mut(&mut pool).unwrap().id.0 = 256,
            10 => Arc::get_mut(&mut pool).unwrap().id.0 = u16::MAX,
            _ => unreachable!(),
        }
        let (_, graph) = pair(pool.clone(), pin, context(1), &graphs).park();
        let mut registry = registry(vec![graph]);
        if guard != 6 {
            registry.render.pools.leases[render_ids::FIRST as usize] = Some(sent(owner, 3));
        }
        let mut deferred = DeferredBatch::new(1);
        housekeeping(&mut registry, &init(3, 3), &mut deferred).unwrap();
        retained(&pool, &deferred);
        assert_eq!(live.load(Ordering::SeqCst), 1, "guard {guard} must retain backing");
        assert!(ids.lock().reclaim_one(|_| true).is_none(), "guard {guard} must retain graph identity");
    }
}

#[test]
fn consumed_old_release_cannot_release_successor_engine_references() {
    let ids = Arc::new(Mutex::new(render_ids::State::new()));
    let live = Arc::new(AtomicUsize::new(0));
    let graphs = Arc::new(AtomicUsize::new(0));
    let (pool, pin) = pool(&ids, 1, &live);
    let owner = Pools::owner(&pool);
    let old = sent(owner, 3);
    let mut next = Lease::acquire(Some(&old), owner).unwrap();
    next.retire_prepare().unwrap();
    let (_, graph) = pair(pool.clone(), pin, context(1), &graphs).park();
    let mut registry = registry(vec![graph]);
    let slot = usize::from(pool.id());
    registry.render.pools.leases[slot] = Some(next);
    let init = init(3, 7);
    let mut deferred = DeferredBatch::new(1);
    housekeeping(&mut registry, &init, &mut deferred).unwrap();
    retained(&pool, &deferred);
    registry.render.pools.leases[slot].as_mut().unwrap().complete_render(false).unwrap();
    registry.render.pools.leases[slot].as_mut().unwrap().mark_release_sent(7).unwrap();
    housekeeping(&mut registry, &init, &mut deferred).unwrap();
    retained(&pool, &deferred);
    init.consumer.store(7, Ordering::Relaxed);
    housekeeping(&mut registry, &init, &mut deferred).unwrap();
    assert_eq!(deferred.render.len(), 1, "only successor final release permits return");
    deferred.render.clear();
}

#[test]
fn invalid_control_observation_is_not_a_release_proof() {
    for failed in [true, false] {
        let ids = Arc::new(Mutex::new(render_ids::State::new()));
        let live = Arc::new(AtomicUsize::new(0));
        let graphs = Arc::new(AtomicUsize::new(0));
        let (pool, pin) = pool(&ids, 1, &live);
        let (_, graph) = pair(pool.clone(), pin, context(1), &graphs).park();
        let mut registry = registry(vec![graph]);
        registry.render.pools.leases[usize::from(pool.id())] = Some(sent(Pools::owner(&pool), 3));
        let mut init = init(0, fw::channels::DEVICE_CONTROL_SLOTS);
        init.failed = failed;
        let mut deferred = DeferredBatch::new(1);
        assert_eq!(housekeeping(&mut registry, &init, &mut deferred), Err(EIO));
        retained(&pool, &deferred);
        assert_eq!(graphs.load(Ordering::SeqCst), 1);
    }
}

#[test]
fn shared_kept_pool_and_bounded_batches_keep_graphs_and_drop_backing_off_lock() {
    let ids = Arc::new(Mutex::new(render_ids::State::new()));
    let live = Arc::new(AtomicUsize::new(0));
    let graphs = Arc::new(AtomicUsize::new(0));
    let (first, first_pin) = pool(&ids, 1, &live);
    let lease = first_pin.lease;
    ids.lock().pin(lease).unwrap();
    let second_pin = PoolPin { ids: ids.clone(), lease };
    let (second, third_pin) = pool(&ids, 2, &live);
    let (_, one) = pair(first.clone(), first_pin, context(1), &graphs).park();
    let (_, two) = pair(first.clone(), second_pin, context(1), &graphs).park();
    let (_, three) = pair(second.clone(), third_pin, context(2), &graphs).park();
    let mut registry = registry(vec![one, two, three]);
    for pool in [&first, &second] {
        registry.render.pools.leases[usize::from(pool.id())] = Some(sent(Pools::owner(pool), 3));
    }
    let init = init(3, 3);
    let mut full = DeferredBatch::new(0);
    housekeeping(&mut registry, &init, &mut full).unwrap();
    assert!(!first.vacant() && !second.vacant(), "full batch cannot vacate a kept pool");
    let mut deferred = DeferredBatch::new(1);
    housekeeping(&mut registry, &init, &mut deferred).unwrap();
    assert!(first.vacant() && !second.vacant());
    assert_eq!(deferred.render.len(), 1);
    assert_eq!(live.load(Ordering::SeqCst), 2);
    housekeeping(&mut registry, &init, &mut deferred).unwrap();
    assert!(!second.vacant(), "room is reserved before any backing is removed");
    deferred.render.clear();
    housekeeping(&mut registry, &init, &mut deferred).unwrap();
    assert!(second.vacant());
    assert_eq!(deferred.render.len(), 1);
    assert_eq!(graphs.load(Ordering::SeqCst), 3, "all shared graph objects remain retained");
    assert!(ids.lock().reclaim_one(|_| true).is_none());
    deferred.render.clear();
    assert_eq!(live.load(Ordering::SeqCst), 0);
}

#[test]
fn both_owner_replacement_routes_move_the_original_identity_pin() {
    for foreign in [false, true] {
        let ids = Arc::new(Mutex::new(render_ids::State::new()));
        let live = Arc::new(AtomicUsize::new(0));
        let graphs = Arc::new(AtomicUsize::new(0));
        let (old_pool, old_pin) = pool(&ids, 1, &live);
        let old_slot = old_pool.id();
        let (incoming_pool, incoming_pin) = pool(&ids, 2, &live);
        let old_context = context(1);
        let incoming_context = if foreign { context(2) } else { old_context.clone() };
        let mut pair = pair(old_pool, old_pin, old_context.clone(), &graphs);
        let mut binding = PreparedBinding {
            pool_pin: Some(incoming_pin), previous_context: old_context, previous_owner: pair.owner,
            ordinal: pair.ordinal, owner: 2, context: incoming_context.clone(), pool: incoming_pool,
            memory: if foreign { Some(memory(incoming_context, &graphs)) } else { None },
            aliases: None, kicks: None, initial_packet: 2,
        };
        pair.bind_owner(&mut binding, &Allocator(PhantomData), &KernelObject(0)).unwrap();
        assert!(binding.pool_pin.is_none(), "incoming pin transfers, not duplicates");
        assert!(ids.lock().reclaim_one(|_| true).is_none(), "replacement must retain outgoing identity");
        let retired = if foreign { pair.displaced.take() } else { pair.previous.take() }.unwrap();
        assert_eq!(retired.pool().id(), old_slot);
        assert!(retired._pool_pin.is_some(), "retired binding owns the original pin");
        assert_eq!(retired.wait_for_pool_release, !foreign);
        drop(retired); // The existing collector's acknowledged-release boundary.
        assert_eq!(ids.lock().reclaim_one(|_| true).unwrap().slot, old_slot);
        assert!(ids.lock().reclaim_one(|_| true).is_none(), "incoming graph still pins its identity");
    }
}
'''


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel-source", type=Path, required=True,
                        help="disposable pinned kernel tree with the retained-pool patch applied")
    args = parser.parse_args()
    mutations = ("omit-kept-graphs", "ignore-final-release", "ignore-owner-epoch",
                 "accept-missing-proof", "drop-parked-pin", "drop-replaced-pin",
                 "ignore-deferred-room")
    with tempfile.TemporaryDirectory(prefix="g17-retained-pools-") as temporary:
        root = Path(temporary)
        for mutation in (None,) + mutations:
            source, binary = root / "retained.rs", root / "retained"
            source.write_text(harness(args.kernel_source, mutation))
            subprocess.run([os.environ.get("RUSTC", "rustc"), "--edition=2021", "--test",
                            "-Dwarnings", str(source), "-o", str(binary)], check=True)
            result = subprocess.run([str(binary), "--test-threads=1"],
                                    capture_output=True, text=True, check=False)
            details = result.stdout + result.stderr
            if mutation is None:
                if result.returncode:
                    raise SystemExit(details)
                print(result.stdout, end="")
            elif result.returncode == 0:
                raise SystemExit("negative control survived: " + mutation)
            elif "panicked at" not in details or "test result: FAILED" not in details:
                raise SystemExit("negative control failed outside behavioral assertions: " +
                                 mutation + "\n" + details)
            else:
                print("PASS: behavioral assertions rejected " + mutation)


if __name__ == "__main__":
    main()
