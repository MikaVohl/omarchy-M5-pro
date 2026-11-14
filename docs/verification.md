# Source-publication verification — 2026-11-14

These checks exercised the publication interfaces, not a new hardware deployment.

| Check | Exercised result |
| --- | --- |
| Public source acquisition | Kernel, bring-up tools, Mesa archives downloaded/hash-verified; Aura and Asahi installer immutable commits fetched/verified |
| Read-only source check | All five prepared source trees/commits verified successfully |
| Converter import | Actual v0.9.1 Multitouch converter imported with all six pinned dependency Git blobs verified |
| Invalid firmware input | Actual CLI refused a wrong local IPSW before creating output firmware |
| Metadata/input regressions | 13 unittest cases passed: partition identities, restore builds, rescue dependency integrity, safe source symlink normalization/tampering, fresh kernel signing bytes/tampering |
| DCP fixed-clock regression | Five scenarios passed under AddressSanitizer/UndefinedBehaviorSanitizer: new/existing providers, wrong type, wrong cell count, missing phandle |
| Syntax/CLI | Python and shell sources parsed; 14 actual preparation/build-helper/deployment CLI help entrypoints exited successfully |
| Rescue/persistent composition | Real composers exercised with isolated synthetic local artifacts: fresh eight-file kernel manifest, loader/patch crossbinding, three rescue plus three root dependencies, fresh GPT/ext4 UUIDs, both full-notch flags; changed kernel refused |
| Greeter configuration | Actual writer exercised in an isolated temporary path: `[General] GreeterEnvironment`, intact variables, mode 0644 |
| Console capture | Actual CLI captured binary bytes through a local PTY, mode 0600, without sending bytes to the target |
| Source provenance | Deployment self-hash provenance regenerated after intentional source changes; release source manifest verifies published files |

The source acquisition check exposed Python 3.14's safe tar symlink normalization; comparison now uses the same `tarfile.data_filter` semantics as extraction. The integrated build manifest check exposed a five/eight-file kernel inventory mismatch; consumers now verify all eight exports and stage all seven nonarchive files required by the runtime guards. Regression coverage accompanies both corrections.

No new clean kernel/Aura/Mesa/root-image build, complete Apple firmware conversion, partition operation, enrollment, physical boot, or SDDM session was performed during publication. Those require the documented build environment, locally acquired matching Apple inputs, and separately authorized physical owner workflow. The original working desktop qualification is recorded in [hardware status](hardware-status.md); it does not certify newly rebuilt bytes.

## Open-network candidate verification — 2026-11-14

- The opt-in patch applied to the pinned kernel with zero fuzz.
- `scripts/test-open-wifi.py` compiled and executed the actual patched join
  builder: open requests omit keys/RSN, unprotected BSS lookup/reporting works,
  secured requests cannot silently downgrade, open 6 GHz is rejected, WPA2 PMK
  and WPA3 SAE payloads remain intact, and extra open association IEs survive.
  These are host checks with kernel-service fixtures, not hardware transactions.
- Shell syntax and unsupported launcher argument rejection passed.
- A complete Fedora 43 ARM64 build with LLVM 21.1.8 and Rust 1.99.1 completed:
  `Image.gz`, J716s DTB, and signed modules for
  `7.3.0-j716s-8ab10100a4c1-open-wifi`.
- All eight exported artifacts passed `sha256sum --check SHA256SUMS`.
  `outputs/kernel-open-wifi-candidate/build-manifest.json` binds the pinned
  commit, patch hash, output hashes, and `unqualified-experimental` status.
- Container CPU limits failed on this bring-up kernel's missing cgroup
  controller; the successful build used `BUILD_RESOURCE_LIMITS=0`.

No candidate was installed or booted. Open-join firmware acceptance, plaintext
RX, DHCP, actual traffic, and physical WPA2/WPA3 regressions remain unverified.
The firmware cipher wire selector is still an explicitly documented hypothesis.

## One-shot trial staging — 2026-11-14

- Fifteen isolated authorization tests passed: exact trial/build binding,
  baseline fallback, downgrade rejection, tampered files/modules, missing/extra
  inventory, unsafe paths/symlinks, and receipt ownership/permissions.
- A zero-fuzz runtime-guard patch applied to the actual older installed guard,
  preserving its existing deployment checks. `--check-only` passed without
  remounting or filesystem growth, before and after staging.
- Baseline root, configured boot, rescue payload and dependency hashes matched.
  The existing FAT marker's first block was 4 KiB aligned and matched direct
  read-only block-device reads.
- The first arming verification exposed stale buffered block-device reads after
  FAT file writes. The trial was cancelled. An aligned `O_DIRECT` read proved
  the discrepancy; the corrected arming command passed direct-disk readback.
- Candidate kernel/modules and separate boot files were staged and validated,
  then the existing `aura-try` marker was armed once. Baseline boot/root/rescue
  files and image identity were not replaced. No reboot was issued.

The loader reports the pinned Aura revision with custom suffixes; its one-shot
behavior is supported by source and
historical marker evidence, not a newly observed recovery boot. Candidate
firmware acceptance and open-network traffic remain unverified until physical
testing.

## Physical open-network trial — 2026-11-14

A physical reboot succeeded and a working guest-network connection was reported.
Live read-only inspection confirmed the running release
`7.3.0-j716s-8ab10100a4c1-open-wifi`, association to an AP advertised without
Wi-Fi security, active `j716s-boot-bind.service`, and the expected
`chosen/aurora,trial` value `aurora/open-wifi.bin`.

No separate throughput, DHCP, roaming, reconnect, or protected-network regression
test was run during this observation. The trial boot succeeded; fallback after
a failed boot has not been physically exercised. The configured baseline remains
unchanged, so the next Linux boot returns to it unless explicitly rearmed.

## Persistent default activation — 2026-11-14

- Twenty-six authorization tests and fourteen isolated filesystem selector tests
  passed, covering persistent boots without a trial property, strict receipt
  binding, selected-payload tampering, atomic publication, failure restoration,
  and rollback with missing/tampered candidate dependencies.
- The actual installed guard upgrade applied with zero fuzz. Its older dependency
  check differs from the published source, so the upgrade patch uses only shared
  selection-check context rather than changing or dropping the older checks.
- Read-only target preflight passed, then authorized activation
  installed the persistent authorization receipt/helper and published the tested
  candidate as `aurora/boot.bin`. Post-publication selection and installed guard
  checks passed.
- Original installed guard/helper bytes and hash records were retained in a
  root-private runtime backup. Baseline `aurora/root.bin`, its dependencies,
  the rescue closure, original deployment receipts and image identities remain
  unchanged. The spent trial marker was not rearmed.

No reboot was issued. Persistent selection is configured and verified on disk;
a subsequent ordinary boot without the trial property has not yet been observed
on hardware. `scripts/select-open-wifi.py --rollback` restores the original
default without requiring valid candidate modules.
