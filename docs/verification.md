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

## Bringup audit improvements — 2026-11-14

- The repo and installed WirePlumber policy match by SHA-256. Actual service
  restart removed the internal fallback playback sink while preserving microphone
  inputs; no speaker playback was performed. Dedicated UCM headphones remain
  exempt; generic fallback playback is not exempt.
- Eight new bringup authorization/publication cases passed. Existing 26 candidate
  authorization and 14 persistent-selection cases passed, including baseline
  fallback and failed-publication restoration. The new cases reject unrelated or
  non-built-in config changes, duplicate config assignments, baseline-config
  tampering, extra patches and wrong trial paths; failures restore the previous
  default rather than silently switching to a different baseline.
- Eleven USB/power/ALS fixture checks and six media image/package parser checks
  passed. The actual read-only port/suspend campaign plan and baseline snapshot
  ran; missing ALS/calibration and physical qualification limits remain recorded.
- A complete Fedora 43 ARM64 LLVM 21.1.8/Rust 1.99.1 bringup kernel build passed.
  All eight exported artifact hashes passed. Compared to the image-bound baseline,
  its config changes only BT_BNEP, BT_LE, BT_LE_L2CAP_ECRED, BT_RFCOMM and
  INPUT_UINPUT from disabled to built-in. It also carries the open-WiFi patch and
  bounded diagnostic-only G17 recovery patch.
- Actual target preflight, candidate composition, installed authorization and
  post-publication boot-guard checks passed. The unbooted bringup payload was
  selected as `aurora/boot.bin` under explicit owner authorization. Previous
  default/helper/guard bytes are preserved privately; baseline/rescue and the
  spent trial marker were not replaced or rearmed. No reboot was performed.
- The open AVD v5/tier2 image built twice with identical bytes and its upstream
  host queue test passed. Output was 73,728 bytes, SHA-256
  `048ebed7ff8c5b07c095c47e975479a9dbf1f8d392cc6b83c0230f7606bf5828`.
  An interrupted initial installer attempt exposed directory-fd misuse; the
  atomic writer was corrected, its receipt-only attempt rolled back, and guarded
  installation/readback succeeded. No unverified firmware was substituted.
- Under explicit attended authorization, AVD loaded and reported hardware version
  `40020`, open-queue transport and seven frames in flight. A generated two-second
  320x180/24 Hz H.264 baseline clip decoded via VA-API through `/dev/video12`
  and `/dev/media0`; all 48 frame checksums matched software decode. Throwaway
  clips/logs were removed after proof.
- Moonlight 6.2.0 was launched with app-scoped `LIBVA_DRIVER_NAME=v4l2_request`
  and the installed driver directory. It initialized VA-API 1.24, selected the
  native M5 Pro Vulkan renderer and completed its built-in HEVC Main10 probe.
  The actual Wayland window was visually inspected without the reported missing
  hardware-decoder warning. No remote streaming session was started.
- The GPU collector now uses `--boot=UUID` and timezone-normalized epoch windows.
  Actual incident journals and privileged host-memory snapshots were retained
  privately. Its nonzero result records missing PSI/module paths, raced fdinfo
  and no matching coredump; it is not a stability pass. Firmware reason 5, zero
  MMU faults and four retained Hyprland commands are confirmed; cause unresolved.
- Changed shell entrypoints parsed and nine changed Python entrypoints parsed.

Ordinary bringup boot, new Bluetooth profiles, protected Wi-Fi regressions,
USB/PD/display/tunnel attachment, charging, suspend/resume, decoder recovery,
high-resolution/long-stream decode and HEVC pixel correctness remain unqualified.
AVE/ANE/camera/ProRes guards remain intact. Same-unit ALS factory input is absent;
the impossible-looking finite TTPD value is preserved, not fabricated into a
temperature. No reboot, suspend, speaker playback, repartitioning or Apple
security-policy change occurred.


## Ordinary bringup boot check — 2026-11-14

- Owner-reported restart; running release is `7.3.0-j716s-8ab10100a4c1-bringup`.
  Actual boot-binding service completed with status 0; no trial DT property.
- No failed system/user services or Hyprland config errors. Native G17S Vulkan
  and DCP enumeration passed; panel is 3456×2234 at 120 Hz, private Mesa/zink
  compositor environment retained, Wi-Fi associated with bidirectional traffic.
- AVD automatically exposed video/media nodes. Hardware VA-API H.264 decode
  matched software checksums for all 48 frames of a fresh two-second test clip.
  Temporary clips, logs and checksums were removed.
- Running kernel enables RFCOMM/LE/BNEP/uinput; an unprivileged RFCOMM socket
  opened and closed without making a connection. The paired headset is down;
  no headset playback/microphone/media-control qualification was performed.
- Internal speaker output remains blocked while microphone inputs remain.
  No GPU-recovery, AVD timeout/error, Oops or SError entries found in this early
  boot after exercise. No long-soak stability or additional codec claim.
- A separate privileged manual guard/BNEP socket command timed out without
  output. The actual boot service, not that command, is the boot-binding proof.
  No SEP probe, suspend, speaker playback, extra reboot or configuration change.

This observation supersedes the earlier unbooted bringup-default status only;
remaining physical-device, Bluetooth-profile, recovery and firmware-ABI gaps
remain unqualified.


## Local source checkpoint — 2026-11-14

The checkpoint checks passed: 78 repository authorization/metadata/DT cases,
six media-firmware parser/security cases and eleven USB/power/ALS fixture cases.
The already staged SEP candidate passed the actual read-only check from the
running bringup kernel, including the installed boot guard and exact DT delta;
it remains unarmed and the persistent default is unchanged.

The staging preflight now admits the explicitly receipt-validated bringup
release alongside baseline/open-WiFi, avoiding an unnecessary rollback solely
to inspect the staged probe. No arbitrary kernel release is admitted.
SEP source/patch/tests are included in the publication inventory. Generated SEP
kernel outputs and the disposable patched source tree are explicitly ignored;
Apple firmware, module-signing keys and private target receipts remain excluded.
Privileged RFCOMM and BNEP protocol sockets opened and closed successfully;
no remote connection, pairing change, network tunnel or audio playback occurred.


Hardware headset listening/microphone/media controls, actual Sunshine/Moonlight
streaming and repeated ordinary boots still require operator participation.
The source checkpoint does not promote SEP discovery to Touch ID support.


## Attended qualification — 2026-11-14

- Owner-authorized headset connection succeeded; A2DP SBC-XQ and HFP profiles
  enumerate. The owner confirmed audible louder test audio and a play/pause toast
  after pressing headset controls. The 25-second AVRCP reader captured no events;
  no capture-based key/volume result is claimed.
- The HFP mSBC recording/playback stalled. PipeWire logged Bluetooth transport
  errors. The private recording was removed and stereo restored; clean
  reconnection recovered after a transient endpoint-in-use error. Pinned Centauri
  send/receive code has no SCO transport, so microphone audio remains unsupported
  despite working RFCOMM signalling. No firmware-ring guess or suppressed error.
- A paired Sunshine Desktop was resumed without quitting the host app. Moonlight
  requested/received 1920×1080/60 at 20 Mbit/s with hardware H.264 forced. Logs
  showed real video packets, AVD VA-API decode and EGL/GLES rendering; the owner
  reported a good desktop stream. Client lifetime was 4m38s, then it was closed.
- No GPU recovery, AVD timeout/error, Oops or SError found during the inspected
  interval. This does not establish prolonged stability or other codec coverage.
- Remaining operator work: repeated ordinary boots, sustained streaming/reconnect
  and the separately attended SEP cold-boot discovery. SEP remains unarmed;
  its SPI sensor, enrollment, persistent selection and enclave writes stay off.


## SEP trial arming verification — 2026-11-14

- Twenty-six SEP authorization/DT/marker cases and fourteen existing persistent
  selection cases passed. Nine new marker cases cover exact in-place updates,
  stale disk readback, concurrent marker/default changes, interruption, flush
  errors, symlinks and cancellation without candidate modules/receipts.
- The real CLI refused arming without attended-cold-boot and backup-attestation
  or waiver flags before touching target files. The staged candidate's actual
  read-only check passed from the running bringup kernel.
- A throwaway live-identity preflight with simulated marker bytes accepted a
  reviewed spent SEP marker and rejected an armed one; no file was written by
  that simulation. This establishes rollback-gate compatibility, not a trial boot.
- Owner explicitly waived the backup after the shared SSD/SEP risk was stated;
  physical recovery and saved work were confirmed. The private arming record
  stores `waived-by-owner`, never a verified-backup claim.
- Actual one-shot arming passed the live guard, full candidate receipt/module
  validation, exact DT delta, protected-file invariants and direct-disk readback.
  Only the existing trial marker and private arming receipt changed. Bringup
  default, baseline/rescue, guard and dependencies were preserved.
- No reboot, SEP registration, sensor access, enrollment or enclave-storage write
  occurred. Trial boot/registration remain pending the owner's attended cold start.


## Observed SEP trial boot and discovery — 2026-11-14

- Exact `sep-probe` release, matching trial property and all six non-writing
  parameters observed after the owner's start; the boot-binding service passed.
  The loader supplied SEP firmware and 9,025/2,898-byte boot/policy manifests.
- The initial guarded command duplicated kmod's command-line parameters. All
  five Rust safety parameters were refused by write-once `SetOnce` storage;
  no module/driver initialization occurred despite modprobe returning 0.
  The rejected command/result/journal was retained, not treated as a pass.
- A corrected dry-run proved exactly one copy of each safe option. One actual
  `modprobe apple_sep` discovery attempt was recorded before loading. The module
  loaded, the J716s profile bound and the driver enabled the SEP/DART changeset.
- xART metadata read reported 170 slots, 12 live records, zero malformed,
  duplicate or repaired records, writes disabled. The transport advertised seven
  initial endpoints in 18 messages, then 12 endpoints; ready was reported in
  1,442 ms with the existing key store open. hwrng registered after that survey.
- The final ready-phase journal was saved privately. No failed system/user
  services and no biometric device nodes were observed. No SEP device/sysfs
  operation, sensor access, complete_attach, enrollment or manual keybag write
  was issued. Host-state persistence remains deferred in keystore-only mode.
- The persistent bringup payload stayed unchanged. Registration was not retried
  and the successful module was not unloaded. A full power cycle and retained
  macOS/Touch ID check are still required operator follow-through.

This is successful SEP transport/discovery on the tested board/build, not Touch
ID enrollment, matching, persistence or complete key-store/API qualification.

