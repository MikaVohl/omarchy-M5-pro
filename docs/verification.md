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
