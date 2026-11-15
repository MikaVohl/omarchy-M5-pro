// Copyright (c) 2026 The Aurora Silicon Contributors
// SPDX-License-Identifier: MIT

//! Collection release and effective rendering law are separate identities.
//! Every bundle and measurement below is public synthetic test data.

mod common;

use aurora_speaker_tuning::bundle::Bundle;
use aurora_speaker_tuning::tuning::{Options, Tuning};
use aurora_speaker_tuning::voicing::parity::{self, Check};
use aurora_speaker_tuning::Diagnostics;
use aurora_speaker_tuning::plist::{binary, Value};
use common::*;
use serde_json::json;

fn tuning(tag: &str) -> Tuning {
    let mut files = spp3_files(4);
    let graph = files.keys().find(|k| k.ends_with(".dspg")).unwrap().clone();
    let text = String::from_utf8(files[&graph].clone()).unwrap();
    // A complete synthetic chain: no unrelated VarEQ preset gap hides admission.
    assert!(text.contains("box Eq (aufx vrq2) 1 1"));
    files.insert(graph, text.replace("box Eq (aufx vrq2) 1 1", "box Eq nfnp 1 1").into_bytes());
    let strip = files.keys().find(|k| k.ends_with(".austrip")).unwrap().clone();
    let protection = effect("Prot", "spp3", preset("spp3", "prot_test", &spp3_state(4)));
    files.insert(strip, binary::write(&dict(vec![(
        "strips", Value::Array(vec![dict(vec![("effects", Value::Array(vec![protection]))])]),
    )])));
    let dir = scratch(&format!("native-release-{tag}"));
    write_dir(&dir, &files, &manifest_json(&files, "26C5035r"));
    let bundle = Bundle::open(&dir).unwrap();
    let mut t = Tuning::load(&bundle, &Options::default(), &mut Diagnostics::default()).unwrap();
    assert!(t.verified);
    // Model the reference-digest optimisation without using private Apple data.
    t.law_build = Some("25G83".into());
    t
}

fn release(t: &Tuning, build: &str, verdict: &str) -> serde_json::Value {
    json!({"build": build, "reference": format!("synthetic {build} Audio Units"),
           "entries": [{"key": parity::key(t).unwrap(), "aid": ACOUSTIC_ID,
                        "verdict": verdict, "reason": "synthetic gate result",
                        "worst_louder_db": 0.0, "worst_quieter_db": 0.0}]})
}

fn steps(t: &Tuning, releases: Vec<serde_json::Value>, law: &str) -> Vec<Check> {
    let document = json!({"format": parity::FORMAT, "law": law,
                          "tolerance": {"louder_db": 0.25, "quieter_db": 1.0}, "releases": releases});
    let bytes = serde_json::to_vec(&document).unwrap();
    let (key, checks) = parity::run(t, Some(&bytes), &mut Diagnostics::default());
    assert_eq!(key.as_deref(), Some(parity::key(t).unwrap().as_str()));
    checks
}

fn macos_step(checks: &[Check]) -> &Check {
    checks.iter().find(|c| c.name == "macos-parity").unwrap()
}

#[test]
fn native_collection_selects_native_attestation_without_changing_render_identity() {
    let t = tuning("native");
    let key = parity::key(&t).unwrap();
    let checks = steps(&t, vec![release(&t, "25G83", "refused"), release(&t, "26C5035r", "pass")], parity::LAW);
    assert!(checks.iter().all(|c| c.result != "refused"), "{checks:?}");
    assert!(macos_step(&checks).detail.contains("synthetic 26C5035r Audio Units"));
    assert_eq!(t.macos_build.as_deref(), Some("26C5035r"));
    assert_eq!(t.law_build.as_deref(), Some("25G83"));
    assert_eq!(parity::key(&t).unwrap(), key);
}

#[test]
fn older_release_pass_cannot_admit_a_native_collection() {
    let t = tuning("older-only");
    let checks = steps(&t, vec![release(&t, "25G83", "pass")], parity::LAW);
    let check = macos_step(&checks);
    assert_eq!(check.result, "refused");
    assert!(check.detail.contains("macOS 26C5035r has no parity attestation"), "{check:?}");
}

#[test]
fn missing_collection_build_cannot_fall_back_to_the_rendering_law() {
    let mut t = tuning("missing");
    t.macos_build = None;
    let checks = steps(&t, vec![release(&t, "25G83", "pass")], parity::LAW);
    let check = macos_step(&checks);
    assert_eq!(check.result, "refused");
    assert!(check.detail.contains("names no macOS build"), "{check:?}");
}

#[test]
fn failed_native_reference_and_stale_rendering_law_still_refuse() {
    let t = tuning("failed");
    let checks = steps(&t, vec![release(&t, "25G83", "pass"), release(&t, "26C5035r", "refused")], parity::LAW);
    assert_eq!(macos_step(&checks).result, "refused");
    assert!(macos_step(&checks).detail.contains("synthetic gate result"));
    let stale = steps(&t, vec![release(&t, "26C5035r", "pass")], "0000");
    assert_eq!(macos_step(&stale).result, "refused");
    assert!(macos_step(&stale).detail.contains("other rendering code"));
}

#[test]
fn reference_release_collections_keep_their_original_gate() {
    let mut t = tuning("reference");
    t.macos_build = Some("25G83".into());
    let checks = steps(&t, vec![release(&t, "25G83", "pass")], parity::LAW);
    assert!(checks.iter().all(|c| c.result != "refused"), "{checks:?}");
    assert!(macos_step(&checks).detail.contains("synthetic 25G83 Audio Units"));
}
