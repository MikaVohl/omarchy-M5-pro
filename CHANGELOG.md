# Changelog

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
