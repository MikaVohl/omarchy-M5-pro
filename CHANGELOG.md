# Changelog


## Guarded ANE startup and real H17 execution - 2026-11-15

- Boot the cross-built guarded acquisition kernel with live firmware identity, cold ownership and ordered DART/DAPF checks intact.
- Complete one real H17 fp16 addition: 64/64 lanes bit-exact, maximum 0 ulp, with LOAD/CREATE/TERMINATE/UNLOAD evidence.
- Fix result verification and fixture lengths to the 4096-byte HWX/ABI tensor span rather than 16-KiB allocation capacity; verify the saved output without rerunning hardware.
- Retain strict numerical/length checks and add allocation-copy rejection coverage; do not claim general model support or sustained stability.

## Live ANE identity and cross-build backend - 2026-11-15

- Observe the exact admitted live ANE TEXT hash on the corrected metadata kernel without starting the engine.
- Separate authorized acquisition boot policy from observation and migrate every caller; retain cold-state, ownership, stage-order and quarantine gates.
- Share a pinned native-build/cross-build worker and locally validate returned architecture, release, signed modules, hashes and target DT preservation.
- Launch a measured, 64-job EPYC cross-build with public inputs only; retain target data and deployment on the Mac.
- Fix the cross userspace-link prerequisite with a real signed ARM64 sysroot and bound package/flag provenance, preserving byte-identical kernel config checks.
- Separate public source-cache identity from build metadata and refuse legacy unowned caches; preserve completed toolchain setup for the corrected run.
- Complete the 64-job native cross-build, transfer and local finalization in 402.7 seconds with prepared toolchain; validate returned ARM64 Image and signed modules without config drift.
- Archive spent SEP trial payloads with verified restoration data to free ESP space, then select the owner-authorized guarded acquisition candidate with observation/media/rescue rollback preserved; no automatic reboot or ANE job.

## Hardware video encoding and HEVC decoding - 2026-11-15

- Boot the integrated kernel and qualify 30-frame AVE H.264/HEVC Main encode with software decode and reference PSNR, plus bit-identical 30-frame AVD HEVC Main decode.
- Install required GStreamer plugins and correct read-only dynamic encoder device binding; compare actual reference pixels rather than only output lengths.
- Record and preserve the speaker startup xrun refusal without relaxing checks or resetting counters.
- Identify missing ANE type metadata from the actual bound ADT; prepare a J716s-only exact-property observation variant with existing identity/ownership gates preserved.
- Complete exact-type ANE observation kernel build and target validation; select it with owner authorization while preserving the tested full kernel/runtime and rescue. No automatic reboot or engine startup.

## Broad guarded bringup candidate - 2026-11-15

- Prepare exact-build AVE compatibility admission and ANE build/IPC support with mandatory observation-only startup and firmware-requested parser bounds.
- Integrate DP tunnel withdrawal handling and M5-safe retained GPU pool reclamation; preserve source-pinned working kernels, sensor/KIS DT and rollback.
- Strengthen private native camera/voice inspection and unit-bound Bluetooth acquisition; retain hardware refusal where takeover, privacy or routing contracts remain unresolved.
- Record proved 18-core thermal effort mapping without activating unresolved PMP/report or manual fan control.
- Archive unreferenced ESP diagnostics with owner authorization and hash readback; preserve boot/rescue dependencies and record microphone-array data flow without speech-quality claims.
- Complete the integrated Image/modules build and exact target checks; select the candidate for a later owner-issued boot with the working default/runtime and rescue preserved.
- Build the pinned open H17 compiler and emit a real addition HWX plus driver LOAD sections; retain private preparation hashes without claiming ANE execution.

## Legacy HFP hardware results - 2026-11-15

- Observe the HFP candidate boot and automatic protected speaker startup; pass fresh same-kernel wiring, native voicing and live 512-frame graph verification.
- Confirm legacy synchronous setup selection on hardware, but record controller 0x11 rejection for both transparent/mSBC and CVSD voice settings, with no SCO traffic or recorded samples.
- Restore normal audio routes and clear recording buffers. Keep microphone support unavailable; native vendor-routing source clues do not justify guessed commands, resets or another speculative kernel.

## AirPods synchronous setup candidate - 2026-11-15

- Capture actual controller rejection of enhanced HFP synchronous setup (0x11), with no SCO traffic; distinguish a transient source from microphone success.
- Restore normal audio routes and retain filtered private transport evidence without publishing keys, addresses or audio.
- Add an isolated pinned `touchid-sco-spk-hfp` recipe using the upstream controller-scoped legacy-setup quirk; preserve protected speakers, Touch ID, baseline/rescue and predecessor rollback.
- Pass 51 authorization regressions, zero-fuzz patch preflight and the production capability predicate's host cases; require full build, target checks and a manual hardware boot before claiming voice.
- Complete the full legacy-setup kernel build and target preflight; select it with owner authorization while preserving the working speaker default/runtime and rescue. No automatic reboot or hardware voice claim.

## Protected speaker playback - 2026-11-15

- Boot the protected-ceiling kernel and pass fresh native voicing, silent wiring and 512-frame graph checks.
- Observe fail-closed lease/owner-death withdrawal with zero-only audio, then ordinary restart and clean graph recovery.
- Confirm owner-heard Mac speaker tone with live protection; persistently enable the guarded preview stack without claiming volume-sweep, lifetime, suspend or repeated-boot certification.
- Defer AirPods microphone qualification at the owner's request; no HFP switch or recording performed.

## Guarded runtime and ALS enablement — 2026-11-15

- Integrate native-capture repairs, install genuine ALS calibration and observe finite changing illuminance reports after targeted child-driver activation.
- Add pinned, receipt-bound speaker runtime preparation/install/rollback with real numerical admission, complete upstream helpers and explicit preview activation.
- Separate collection attestation release from effective rendering law without changing the measured law/key; fix v2 unit identification and preservation of declared BuzzKill matrix assets.
- Observe current-kernel full-blob silent and live graph passes, then the supervisor's fail-closed −30 dB operating-cap refusal; retain muted safety rather than weakening readback.
- Add an isolated protected-only J716s operating-ceiling kernel variant while preserving legacy/global/floor/lease gates; require an owner-issued restart and fresh hardware qualification.
- Capture actual read-only HCI device counters alongside privacy-filtered SCO metadata, without fabricating transport or microphone success.

## Paired-macOS acquisition — 2026-11-15

- Fix portable speaker reference tuning-root paths in preparation, native capture and comparison; capture a genuine same-unit six-channel native AudioUnit reference without audio-device access.
- Admit the native camera daemon's root-owned hardlinks only on read-only System storage; retain private-package single-link, symlink, bounds and source/identity guards with boundary regressions.
- Recover genuine 193-byte ALS CalibrationData and preserve camera/media native evidence privately; keep installation, activation, controller parity and first-DMA qualification separate.
- Pass the existing full-volume speaker voicing gate against native macOS on all 182 measured cells using the original hash-bound ARM64 compiler and a matching-source ARM64 renderer; keep the private attestation uninstalled and playback guarded.

## Speaker-priority native bringup — 2026-11-14

- Recognize the exact T6050 speaker transport and observe six-amp/twelve-sense silent hardware wiring, lease-expiry and owner-death containment; keep audible playback guarded pending native DSP/controller parity.
- Add same-unit, source-pinned native speaker reference preparation/capture/comparison without audio-device access; record full-volume-only scope and private-data restrictions.
- Derive standard Centauri SCO from exact native profile/code/cache evidence; implement TX/RX, tagged ownership, ring-full handling and fail-closed lifecycle without altering HCI credits or guessing firmware rings.
- Add a Touch ID-preserving `touchid-sco` build/deployment variant with strict config/DT/pin authorization and prior-Touch-ID rollback.
- Quarantine uncertain ProRes DMA without shared reset or buffer/PM/scheduler release; retain first-DMA gates. Record exact AVE Process-size extensions and unresolved ANE relocation/boot contracts.
- Add native camera/ALS acquisition and enforce the observed 193-byte illuminance contract; refuse direct HmCA conversion/installation and keep camera ownership/privacy unqualified.

## Qualification record integration

- Integrate the archived factory-data session `2026-11-15T03-50-42-200Z_01a1dd54-2718-7505-9803-008623374b39`; retain its existing source and detailed records without repeating hardware tests.
- Consolidate speaker offline compilation, ProRes/camera preparation, ALS HmCA non-equivalence and AVE/ANE mismatch evidence in the feature matrix; keep hardware safety and ABI gates explicit.
- Record 63 executed targeted tests and 13 additional host-fixture assertions, not new hardware passes; correct the README's stale awaiting-first-bringup-boot summary.
- Record basic Touch ID enrollment, matching and real Quickshell fingerprint PAM unlock; retain sudo/polkit, reboot-matching and macOS-return qualification boundaries. Document the pinned v4/v5 libfprint build and its explicit repository-package cutover.

## Factory-data bringup progress — 2026-11-14

- Recover complete exact-unit AID41 speaker tuning and factory calibration from retained macOS 26C5035r using a patched, read-only APFS reader; add hash-pinned preparation/verification with cross-unit/vendor/build refusal.
- Validate the unmodified speaker bundle with a schema-compatible offline reader and compile a unit-bound six-channel Spp3 blob. Keep playback guarded: this macOS build lacks differential parity qualification and the new protection stack remains alpha.
- Preserve exact-build camera tuning and current ISP handoff provenance; refuse stale physical-range reuse and keep sensor identity/privacy/ABI qualification explicit.
- Preserve same-unit ALS HmCA factory material without mislabelling it as CalibrationData; add boot-guarded, hash-anchored installation/rollback for genuine IORegistry CalibrationData only.
- Add passive Bluetooth voice-negotiation/SCO-header capture with mandatory locked kernel filtering, without recording audio or claiming SCO delivery.
- Extract exact 26C5035r/T6050 AppleProResHW 627 native init tables into a pinned private APRI candidate; keep installation/startup outside preparation.
- Add an isolated AVE/ANE diagnostic patch that distinguishes actual/expected geometry and measured/unmeasured TEXT digests without broadening acquisition guards; exercise the exact ANE-layout refusal in the production handoff fixture.

## Touch ID kernel and Omarchy integration — 2026-11-14

- Add `kernel-j716s-touchid.patch`: J716s Touch ID profile (J714s protocol, `0x1c000` capacity), refusing trusted keys, the FileVault API and an unguarded xART.
- Add the persistent `touchid` variant (bringup config, build-bound sensor DTB) and `deploy-bringup.py --variant touchid` with an exact DT-delta check and rollback.
- Add the omarchy-mac overlay (J716s eligibility, multi-name `firmware-name`, driver policy, pacman re-apply hook) so Omarchy's own fingerprint setup drives enrollment.
- Let the boot guard accept the running default while a newer authorized default awaits reboot.

## Sensor-authentication SEP candidate — 2026-11-14

- Add an isolated `sep-sensor` kernel variant with one bounded, opt-in, non-enrolling sensor identity/authentication probe; the build guest runs the actual Rust admission fixture.
- Add same-unit factory material preparation that refuses without forwarded local-policy bytes and records only a pinned label/mBoot profile match, not restore-build verification.
- Stage `sep-sensor` with an exact DT-delta check (pinned SPI2/GPIO wiring plus path-matched phandle renumbering) and receipt-bound factory material with full rollback.
- Add a guarded `--update-validator` mode; the installed trial validator now matches the repository and still admits the staged discovery candidate.
- Observe one enclave-accepted J716s sensor authentication session on the attended `sep-sensor` trial; enrollment and matching remain unimplemented.

## Discovery-only Touch ID research candidate — 2026-11-14

- Record official and experimental M-series support, J716s driver-admission refusal, live sensor/SEP wiring, read-only APFS/factory-data results, and host-only Aura handoff proof.
- Add an isolated J716s SEP profile with measured `0x1c000` registration capacity, mandatory non-writing parameters, and a refused transition to sensor/persistent operation; leave J714s and baseline reproduction unchanged.
- Add a pinned build recipe and non-arming staging tool, preserving baseline configuration, qualified KIS topology, current default and rescue. Bind changed DTB/modules to the build receipt and forbid persistent selection of this probe.
- Add isolated authorization regressions. SEP attachment, enrollment and matching remain unqualified; the attended reboot is a separately authorized experiment.
- Add explicitly attended one-shot SEP arming/cancellation, private backup attestation/waiver, direct-disk marker readback and failure restoration; keep persistent selection forbidden.
- Preserve baseline/bringup rollback after a reviewed SEP marker is spent; reject armed and unreviewed trial markers.
- Add isolated marker durability, interruption, concurrent-change and missing-module cancellation regressions.
- Record the observed J716s cold-boot SEP discovery (12 endpoints, 1442 ms ready) and correct the load procedure to avoid duplicated Rust write-once parameters; keep sensor/enrollment/persistence disabled.

## Bringup audit improvements — 2026-11-14

- Close the WirePlumber fallback/pro-audio speaker-route bypass while retaining microphone inputs and dedicated UCM headphone exemption.
- Add an isolated Bluetooth-capable kernel variant and bounded diagnostic-only G17 recovery snapshots; preserve open-WiFi behavior, baseline/rescue and prior-default rollback.
- Add exact-profile open AVD firmware building, package validation and guarded installation/removal; verify hardware H.264 frame checksums and Moonlight's app-scoped VA-API startup path.
- Add read-only per-port USB/PD/DP/USB4, charging and suspend evidence, plus same-unit factory ALS calibration capture/preparation.
- Record unsupported AVE/ANE/camera/ProRes ABI boundaries rather than weakening hardware guards or claiming enumeration as qualification.

## Experimental open-network Wi-Fi — 2026-11-14

- Add an opt-in T6050 plaintext join candidate, isolated kernel release/output and patch-hash provenance; leave baseline reproduction unchanged.
- Add host execution checks for actual join construction, downgrade rejection, BSS privacy, WPA2 PMK, and WPA3 SAE.
- Record exact-firmware enum evidence and unresolved wire/RX semantics. Hardware support is not yet qualified.
- Add receipt-bound one-shot staging, cancellation, read-only guard checks, and candidate module validation while preserving the configured baseline and rescue.
- Add explicit persistent activation of the physically tested payload, strict default-selection receipts, publication-last rollback handling, and baseline-restoration tooling.

## Initial source publication — 2026-11-14

- Package the J716s/Mac17,8 exact-26C5035r kernel, Aura, firmware-conversion, rescue, macOS preparation/enrollment, root-image, and persistent deployment sources.
- Replace original per-machine identities and historical output-byte assumptions with fresh local receipts and source-bound build manifests.
- Include the working DCP fixed-clock handoff, native SDDM greeter environment, explicit-capacity ext4 resize fix, and full-notch boot configuration.
- Keep owner-password setup, protected hardware-stack ownership/removal guards, rescue retention, raw readback, and active-payload-last publication.
- Add a read-only local KIS PTY capture utility and published-source integrity verification.
- Exclude Apple firmware/images, secrets, private signing keys, original machine receipts, and private diagnostics. Document hardware qualification boundaries separately from source-interface checks.
- Keep hardware reports focused on reproduction and qualification rather than owner-specific operational choices.
