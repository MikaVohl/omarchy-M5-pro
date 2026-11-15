# Changelog


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
