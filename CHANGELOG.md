# Changelog

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
