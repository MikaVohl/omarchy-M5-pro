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
  and the successful module was not unloaded. The owner subsequently reported
  normal macOS and Touch ID function after returning from the experiment.

This is successful SEP transport/discovery on the tested board/build, not Touch
ID enrollment, matching, persistence or complete key-store/API qualification.


## Owner-reported macOS return — 2026-11-14

The owner reports macOS and Touch ID work normally following the SEP discovery
trial. No additional hardware check was run to reconfirm that report. This is
one owner-observed recovery result, not a storage-state audit, repeated recovery
qualification, or permission to enable Linux biometric enrollment/persistence.


## Factory-data preparation verification — 2026-11-14

On the running `touchid` kernel, without reboot or hardware enablement:

- `test_speaker_tuning.py`: four targeted identity/build/vendor/read-only-input
  regressions passed. Actual capture and verification produced a complete,
  same-unit AID41/26C5035r package from retained macOS.
- The distributable APFS-reader patch passed zero-fuzz application. The native
  reader actually mounted unencrypted System/Preboot read-only; exact build
  identification and real Apple source reads succeeded. Both mounts were closed.
- Offline `aurora-speaker-tuning` from schema-compatible `54c911c` validated the
  unmodified factory bundle and compiled a 1,668-byte, unit-calibrated Spp3 blob.
  The **unvalidated 26C5035r/reference-25G83 warning remains**. No parity,
  protection-runtime, loudness, hardware-sense or playback pass is claimed.
- `test_bluetooth_sco_capture.py`: eighteen tests passed, including actual Linux
  BPF filtering/truncation of synthetic socket datagrams. A one-second idle hci0
  capture completed with zero control records. No profile or pairing changes,
  voice retry or audio recording; monitor counters do not prove firmware TX.
- `scripts/test-usb-power.py`: eighteen port/ALS tests passed, including new
  guarded calibration installation/rollback paths. Only genuine factory HmCA
  material was recovered; it is **not proven installable CalibrationData**.
  No real calibration installation/AOP restart was performed.
- `test_camera_firmware.py`: seven tests passed. The live private package check
  accepted exact-build daemon/setfile hashes and current handoff provenance,
  while reporting sensor identity unproven, not installed and hardware untouched.

Private bundles, diagnostics and derived protection blobs remain excluded from
publication. The installed speaker policy, masked safety daemon and working
boot selection are unchanged. Preparation checks do not replace attended
hardware qualification or macOS differential validation.


### Encoder/ANE preparation checks

- `scripts/test-media-firmware.py`: sixteen parser/native-pointer/private-input
  regressions passed; no firmware was executed.
- The exact retained-source ProRes candidate check passed: 2,880-byte APRI,
  T6050, native rows 82/80/62/12, pinned final SHA-256. It remains uninstalled.
- `kernel-j716s-media-handoff-diagnostics.patch` applies to the exact clean
  kernel pin without fuzz. A disposable C host harness ran the **production**
  KUnit cold-handoff fixture with the production range/admission predicates:
  thirteen assertions passed, including rejection of 26C5035r geometry even
  with a matching digest. The harness and binary were removed afterward.
- The diagnostic patch has not had a full kernel build, real KUnit boot or
  hardware boot. No claim of live ANE TEXT measurement is made from a retained
  file hash. No AVE/ANE/ProRes engine, module rebind or boot selection ran.

## Factory-data session integration

Results from session
`2026-11-15T03-50-42-200Z_01a1dd54-2718-7505-9803-008623374b39`
are incorporated in the factory-data sections above, setup instructions and
source provenance. The timestamp identifies the archive; the executed checks
were recorded there as 2026-11-14. The integration consulted the parent and
its MediaEngines, SensorsCamera and BluetoothVoice results without repeating
hardware operations.

The parent executed **63 targeted tests**: four speaker, eighteen Bluetooth
capture, eighteen USB/power/ALS, seven camera and sixteen media cases. The
production ANE handoff host fixture passed **13 additional assertions**, not
thirteen additional unittest cases or a full KUnit boot. Real read-only macOS
mounts, speaker collection/verification, offline Spp3 validation/compilation,
camera and ProRes package checks, and a one-second idle Bluetooth metadata
capture also completed. The source session's final release check verified
**148 published files**; that is its historical inventory, not a count for
this integration. No resulting local commit was evidenced in that session.

No new speaker playback/protection runtime, encoder/ANE startup, camera capture,
headset microphone, ALS operation, Touch ID enrollment/matching or PAM check
was performed. The 26C5035r speaker differential warning, firmware acquisition
guards and private-data exclusions remain. The existing detailed results are
retained rather than counted as a second qualification run.

## Touch ID enablement checkpoint — 2026-11-14

- Live `uname -r`: `7.3.0-j716s-8ab10100a4c1-touchid`; platform reports ready.
  The installed v4/v5-capable libfprint package is
  `2:1.94.100.aurora.a227a35-1`. `fprintd-list` reports the enrolled finger.
- Existing Quickshell journal records real fingerprint PAM authentication and
  secure-session unlock at 22:17:26 through `omarchy-lock-fingerprint`.
  This is executed surface evidence, not an inference from setup configuration.
- Actual lock status reports fingerprint available and password PAM present;
  sudo/polkit files have fingerprint, lid gating and password fallback. Those
  two authentication surfaces and fallback execution were not separately tested.
- A new 45-second `fprintd-verify` check timed out without a result. No retry,
  re-enrollment or additional success is claimed. The earlier recorded lock
  authentication is the matching proof.
- No Hyprland configuration errors or failed system services were reported.
  PipeWire exposes only Dummy Output as its sink; microphone sources remain,
  and `speakersafetyd` is masked. No speaker or unqualified engine enablement,
  kernel deployment, reboot or suspend was performed in this checkpoint.

The archived factory-data tests above were not rerun here. Fingerprint reboot
matching and macOS return after Linux enrollment are still unqualified.

## Speaker-priority source and hardware checks — 2026-11-14

- Actual upstream silent speaker self-check passed: six amps, twelve sense
  channels, S24 playback, continuous capture first, zero samples only.
- Separate zero-only lease-expiry and owner-death checks both withdrew the
  grant and stopped playback. Original interface/audio services restored.
- Real same-unit native reference request prepared. Eight reference-admission
  regressions passed; native macOS rendering has not run.
- Camera acquisition: ten tests passed. Port/ALS: 24 tests passed, including
  exact 193-byte admission and direct HmCA/size refusal before publication.
- Media/native parsing: twenty tests passed. Actual retained-source inspector
  established AVE's 16-byte Process extensions and guarded ANE/ProRes facts.
- ProRes production IRQ/watchdog/publication/PM/cancellation harness passed
  under ASan/UBSan; all eight deliberately broken negative controls failed
  assertions. Initial extractor/kernel-doc and missing-ACK observability
  errors were repaired; only the failed harness was rerun.
- Eighteen Bluetooth privacy/capture tests and five exact native acquisition
  tests passed. The production SCO C harness passed after correcting unsigned
  header-size constants in its host boundary. It checks framing, tagged
  completion/ownership, ring-full/wrap, allocation failure, malformed input,
  caller skb ownership, command/ACL behavior and close/flush/reopen.
- Twenty-eight bringup/SCO authorization and fourteen persistent-selection
  regressions passed. Build-shell syntax passed.
- Full Fedora43 ARM64 LLVM21.1.8/Rust1.99.1 `touchid-sco` kernel, DTB and signed
  modules built. All eight artifacts plus build manifest/provenance hashes
  passed. Exact config, baseline KIS topology and build-bound sensor DT delta
  passed the real read-only target composition/boot-guard checks.
- Actual pinned native Bluetooth acquisition succeeded from the retained
  read-only System/Cryptex, without radio/audio operation.
- Authorized persistent publication passed the real composition, exact-DT,
  baseline/rescue/partition/config/module binding and installed boot-guard
  checks. Prior Touch ID/default/runtime bytes retained; no reboot or marker
  arming. Physical `touchid-sco` boot remains unobserved.
- An encrypted private transfer bundle roundtripped byte-for-byte on Linux;
  native macOS decryption/capture is not yet exercised. Generated inputs and
  transfer key remain outside publication.

No speaker DSP/controller parity, nonzero playback, headset microphone,
encoder/ANE startup, camera capture or ALS activation is claimed. New kernel
qualification requires its own attended boot and hardware checks.

## Paired-macOS acquisition checks — 2026-11-15

- SSH host identity was verified independently; actual Darwin/model/build and
  same-unit identity passed before hardware acquisition.
- The native speaker capture reproduced a nonexistent `tuning/audio-tuning`
  path. Preparation, capture and comparison now use the actual portable
  `tuning/` root. The repaired capture produced 1,159,200 frames in six lanes;
  external manifest, complete file inventory, finite samples and native-not-
  modelled reference admission passed after transfer.
- ALS capture recovered one genuine 193-byte CalibrationData. Preserved platform,
  ALS plist and capture receipt hashes matched; no HmCA conversion or install.
- Camera capture reproduced a valid native System daemon with two hardlinks.
  Explicit root-owned/read-only System admission repaired it without relaxing
  private-package isolation. Repaired acquisition and all transferred manifest
  byte counts/source hashes passed; no sensor/privacy/ABI qualification claim.
- Camera regressions passed, including default/private hardlink refusal and
  root-owned/read-only admission with writable/non-root refusal. The first run
  lacked the pinned ADT parser; its exact source/hash prerequisite was restored,
  then only the dependency-blocked test groups were rerun. Eight existing native
  reference-admission cases passed.
- Original media acquisition succeeded. Its externally anchored bundle passed
  `capture-media-macos.py --check`; all 13 source records survived transfer.
  Empty ANE name queries and unresolved native engine contracts remain explicit.
- The original hash-bound ARM64 compiler and matching-source ARM64 renderer
  executed under QEMU with a digest-pinned GNU loader/runtime. The full-volume
  native comparison passed all 182 measured cells within +0.25/-1.0 dB;
  observed extrema were +0.184/-0.048 dB. Renderer build identity was
  aarch64-unknown-linux-gnu, Rust 1.91.1; no compiler binding was replaced.
  This is an offline voicing pass, not controller/volume/graph/safety or playback
  qualification. The private attestation remains uninstalled.
- Native inputs, failure logs and returned manifests remain private on both
  machines. No Linux hardware identity was simulated and no reboot, microphone,
  nonzero speaker, camera/encoder/ANE job or ALS activation was performed.

## Live enablement preparation — 2026-11-15

- Returned native speaker/camera manifests and parity file matched their
  independent hashes; full reference inventory and target camera admission
  passed without starting a camera.
- Genuine 193-byte ALS calibration passed unit/build/boot-guard/package checks,
  installation and readback. Targeted ALS child rebind produced changing finite
  illuminance reports after bounded first-report waiting. No AOP reset/reboot;
  absolute accuracy and colour temperature remain unqualified.
- Fifteen reference-admission, fourteen runtime package and twenty-two SCO
  capture/privacy cases passed. The new collector's read-only HCIGETDEVINFO
  snapshots ran against the actual idle controller without audio/state changes.
- Five Rust native-release selection regressions and seven unit-identification
  cases passed. Four asset-preservation regressions passed, including complete
  fallback roundtrip and missing/corrupt/truncated/unknown-format refusal.
  Incorrect synthetic fixture prerequisites were fixed, not hidden by narrowing
  numerical assertions or stripping metadata.
- The pinned runtime compiled and admitted the actual native law/key/reference;
  full Spp3/voicing parameters, source/patch/test hashes and package inventory
  were recorded privately. Actual isolated-root install/receipt rollback passed.
- Current-kernel full-blob silent wiring and complete live graph verification
  passed. Upstream preview switching used ordinary APIs and a qualified user
  helper, not a manual marker. The supervisor's real −30 dB operating mismatch
  withdrew protection; the nonzero attempt stopped before playback.
- Thirty-nine candidate authorization cases and shell syntax passed for the
  protected-ceiling variant. Its actual patched kernel max-volume/gain/start
  bodies passed ASan/UBSan host checks preserving legacy/global/floor behavior.

The new kernel and headset microphone are not hardware-qualified by these
checks. The headset failed paging before any SCO/HFP test; an owner-ready
device is required before another attempt. No nonzero speaker, encoder/ANE or
camera job is claimed.

- The complete protected-ceiling kernel build finished; target preflight passed
  exact DT/config/build, guard, partition and ESP checks. Owner-authorized
  persistent deployment and installed guard validation passed. Previous
  default/runtime and baseline/rescue were preserved; no reboot/trial arming.
- Withdrawal readback: graph/session user services inactive and disabled,
  legacy safety masked, `Speaker Protection=0`.
- The owner-ready bounded AirPods connection again failed at paging; no HFP
  profile selection or microphone recording was reached.

## Post-reboot guarded speaker output - 2026-11-15

- Actual `touchid-sco-spk` kernel, successful boot binding, no failed system
  units, and unchanged 3456×2234/120 Hz panel observed.
- Fresh 130,448-byte blob provisioning and bound-source zero-only wiring check
  passed. A first invocation without the required tuning source refused before
  hardware testing. Native voicing and complete live 512-frame graph checks
  passed; five silent pilot starts had no xruns.
- Upstream preview activation granted protection and reported supervising.
  Zero-only pause and owner-death injections observed withdrawal after 32.9 ms
  and 14.7 ms respectively; death readback showed woofer mute and non-running
  PREPARED PCM. Recovery used ordinary supervisor restart and a fresh graph;
  complete silent verification then passed.
- A first submitted tone was muted, so no acoustic success is attributed to it.
  The explicitly unmuted two-second 660 Hz tone at peak 0.15 completed, and the
  owner confirmed hearing it from Mac speakers. Protection remained 1 and the
  supervisor active afterward.
- Persistent system/user startup enablement read back enabled. Repeat-boot,
  suspend, volume-sweep and lifetime certification are not claimed. AirPods
  connected but HFP/microphone testing was deferred by the owner.

## AirPods HFP setup evidence - 2026-11-15

- Successful AirPods connection, advertised voice profiles and transient
  Bluetooth input observed. The mSBC profile request did not establish a
  working voice link; the Bluetooth-only recorder timed out after 18 seconds.
- A 60-second private filtered capture completed without collector errors:
  ten control records, two enhanced setup commands rejected with status 0x11,
  no SCO headers and controller SCO TX/RX counters unchanged at zero.
- A2DP profile and pre-test default routes restored; speaker protection
  remained granted. No raw recording file created; temporary buffers cleared.
- Isolated upstream-quirk patch applies without fuzz. All 51 candidate
  authorization cases passed, including predecessor-build refusal. Shell
  syntax passed; the production enhanced capability macro passed four host
  capability/quirk cases under ASan/UBSan. A first host command found no clang;
  the available cc compiler executed the proof successfully.
- Full candidate kernel build started. No legacy-command or microphone
  hardware qualification is inferred from the host checks.

- The full legacy-setup kernel build completed. Target preflight passed exact
  build/patch/config/DT, installed guard, partition and ESP-space checks.
- Owner-authorized persistent selection and publication guard checks passed.
  Previous protected-speaker default/runtime and baseline/rescue preserved;
  no reboot or trial arming. Hardware legacy HFP/microphone proof remains pending.

## Legacy HFP hardware results - 2026-11-15

- Actual HFP candidate boot, successful boot binding, automatic supervising
  speaker service and grant observed. Complete new-kernel wiring/voicing/live
  graph verification passed; five 512-frame silent starts had no xruns.
- After an initial paging failure, the owner-ready AirPods connected. Legacy
  setup selection was observed directly: two transparent-mode commands with
  voice setting 0x0003, and a distinct CVSD command with 0x0060. All received
  Command Status 0x11; neither recorder received any samples before its bound.
- Both 60-second filtered captures completed without errors. Ten/six control
  records, zero SCO headers, no synchronous completion and zero controller
  SCO TX/RX deltas were observed. No codec or microphone success is inferred.
- Original profile/default routes restored; empty private recording buffers
  cleared, no raw audio file created. Final speaker grant/service remained
  active; the final card listing enumerated no Bluetooth card.
- Pinned native ARM64e voice configuration was inspected: an ACI 0xfe10
  vendor-command path and dispatch reference were found, but correct live
  T6050 routing/call sequence was not established. No speculative vendor
  command, reset, kernel patch or further recording retry followed.

## Integrated broad bringup source checks - 2026-11-15

- All 15 selected patches composed against the immutable archive without fuzz
  after correcting AVE/G17 patch context counts. No original source/build was
  overwritten. The thermal mapping patch remains excluded for lack of PMP
  startup admission; camera and voice remain hardware-refused.
- Actual AVE admission/builders and ANE admission/reservation/IPC parser bodies
  passed controls and behavioral negative controls. Missing host GENMASK and
  remap-stub indentation were corrected without suppressing compiler warnings.
- Peripheral production wait/owner/token checks passed; six thermal, 64 candidate
  authorization, three ANE output, eleven native capture and five Bluetooth
  acquisition cases passed. Shell syntax passed. Seven G17 production-body
  cases and seven behavioral negative controls passed with pinned Rust 1.99.1.
- Actual private AVE/native media, Bluetooth and camera inspectors ran in
  matching private-owner directories after correctly refusing mismatched-owner
  access. Geometry/CRC and native command differences are explicit; no captured
  file digest was relabelled as a live firmware hash or engine authorization.
- Ten unreferenced ESP artifacts passed copy/readback and archive hash checks;
  selected/default/rescue dependencies unchanged, installed guard passed.
- Approved microphone sample delivered exactly 10 seconds on all three array
  channels, finite low-level audio on 0/1 and zero on 2. Recorder exit 1 at its
  sample limit remains recorded; protected playback exit 0. The owner was away,
  so no speech-quality pass is claimed. Audio buffers deleted.
- Integrated kernel build started; no candidate reboot, AVE/ANE job, camera,
  manual fan, physical USB/HDMI/dock/SD test or sleep was performed.

- Initial G17 host extraction failed on namespace/type visibility in the fixture;
  those fixture errors were corrected with compiler warnings still enforced.
  All ownership/release assertions and negative controls then passed. Full
  kernel compilation remains a separate required check.

- Full integrated kernel Image/modules build completed. Exact target patch,
  config, sensor/KIS DT, guard, partition and ESP checks passed; owner-authorized
  persistent selection passed and preserved the working default/runtime,
  modules and baseline/rescue. No reboot or new hardware job.
- Pinned H17 compiler build and actual HWX emission passed after resolving the
  container's missing `which` utility. One 49,152-byte HWX was emitted; production
  driver section extraction passed with IDs 5/6 -> 4 and one task's BAR bindings.
  All preparation artifacts are privately hash-bound; no ANE execution claimed.

## Booted full candidate media results - 2026-11-15

- Boot binding/system-service checks passed; internal panel retained full mode.
  AVE recognized the exact firmware and exposed a video encoder.
- Installed missing standard GStreamer encoder/software-decoder plugins; no
  boot package replacement. Dynamic factory device binding verified explicitly.
- Real H.264 and HEVC Main hardware encode completed, 30 frames each. Software
  decode/reference checks passed on every frame/channel: minimum PSNR 63.75
  and 65.31 dB respectively, with clean inspected kernel windows.
- AVD HEVC Main via stateless VA-API completed 30 frames, all bytes equal to
  software output. No new kernel errors in that test window. No codec fallback
  or new global graphics/sandbox configuration was used.
- Current speaker graph verify refused lifetime speaker ERR 1; sense ERR 0 and
  refused starts 0. Seven boot-evidence parser cases passed. No source startup
  fix is justified, and no counters/qualification checks were bypassed.
- ANE failed before TEXT mapping because DT type metadata is absent. Exact live
  ADT proves type 0x220; J716s-only metadata patch applies without fuzz and its
  production consumer gate checks passed. 78 candidate authorization cases
  passed with observation-only mode required. No live pin or ANE job claimed.

- Exact-type observation kernel Image/modules build completed. Target preflight
  passed source/config/module/boot, exact sensor/KIS DT plus native ANE type,
  partition and ESP-space checks. Owner-authorized persistent selection passed
  and preserved the tested full default/runtime, modules and baseline/rescue.
  No reboot, trial arming, ANE identity measurement or engine job occurred.

## Live ANE observation and cross-build backend - 2026-11-15

- Actual observation kernel boot and binding passed. Recognized reservations
  and layout produced the exact admitted live TEXT hash 11557586...dc6066;
  root readback showed observation Y, no accel node and engine left off.
- Acquisition policy migration passed 92 consumer authorization cases; mode
  0 is accepted only for the new separately bound startup variant. No observation
  parameter write, module reload or engine stage action occurred.
- Remote build host and SSH path were inspected read-only. Native x86-64 to
  ARM64 worker and local finalizer passed three and 33 cases; four archive-return
  cases passed before launching the 64-job remote acquisition build.
- No private target DTB, firmware, raw ADT, capture samples or SSH keys uploaded.
  No hardware deployment or execution proof inferred from remote compilation.

- The first cross-build attempt stopped before Image/modules compilation:
  only CONFIG_CC_CAN_LINK disappeared because the target userspace probe found
  host x86 libc headers. No config bit was forced or difference ignored.
- A real ARM64 sysroot from signature-verified Fedora RPMs linked the actual
  stdio programme as AArch64 without executing it. The corrected worker binds
  sysroot package identities/hashes and exact user compiler/linker flags into
  the receipt and input signature; native builds require null sysroot.
- Five worker and 42 finalizer cases passed for the corrected schema. Source
  caches now bind archive/patches/host toolchain independently of variant and
  config, while build/module caches retain the full binding. The old source
  cache was preserved and not relabelled; the corrected job uses a fresh leaf.
- Compiler setup and signed RPM downloads were retained; only the experimental
  unowned sysroot inside our builder image was removed before worker-owned
  staged extraction. The corrected cross-build job was launched; no hardware
  operation or deployment occurred.

- Corrected eg18 build/transfer/local finalization completed in 402.7 seconds,
  64 jobs with prepared toolchain. Eight returned artifacts passed source,
  target architecture, signature, hash, config and target-DT validation.
- Target preflight initially refused 11 MiB ESP free space without mutation.
  Spent SEP trial payloads were archived with receipt copies and hash readback;
  active observation/full kernels, modules, baseline/rescue and factory inputs
  stayed intact. Installed guard passed before/after; free space reached 72 MiB.
- Target preflight and owner-authorized guarded acquisition selection passed;
  observation/media rollback preserved, no reboot or hardware ANE job. Runtime
  startup/output qualification remains pending the manual hardware return.

## Guarded ANE startup and real H17 output - 2026-11-15

- The owner's manual `bringup-ane-start` boot passed installed boot binding,
  exact live TEXT identity, raw-boot/cold-child evidence and ordered
  preload/SID0/DAPF checks. READY requested IPC `0x10441`; ABI-3 accel0 appeared.
- Private preparation hashes passed before the actual production LOAD/EXEC
  runner, bounded by a 60-second outer timeout. Three surfaces were 4096 bytes;
  LOAD took 3.2 ms. One addition job passed 64/64 fp16 lanes with maximum 0 ulp.
  Observed single-call host latency: 229.3 microseconds, not a benchmark.
- Kernel logs recorded LOAD/CREATE/TERMINATE/UNLOAD, with no ANE/DART fault
  reported through the post-job check. No extra job, reset or mode override.
- The initial independent verifier wrongly expected 16384 bytes. Corrected
  fixtures/verification to the HWX/ABI returned 4096-byte tensor span, retaining
  strict length and all-64-lane checks. The saved real output passed afterward;
  the hardware workload was not repeated to conceal the verifier failure.
- Four deterministic numerical-contract cases passed: exact native-stride
  lanes; empty/truncated/oversized results; allocation-sized copy rejection;
  poison, no-op and last-lane corruption. General models remain unqualified.

## GPUI desktop compatibility diagnosis and launcher verification - 2026-11-15

- Native Vulkan inspection identified Apple M5 Pro G17S Borealis, API 1.4.363,
  conformance version 0.0.0.0, valid BGRA8 presentation formats and advertised
  dual-source blending. Exact wgpu-hal 29.0.4 source hides this non-MoltenVK
  noncompliant adapter with default instance flags; no guard was bypassed.
- A targeted DiskTree native diagnostic emitted its generic adapter/surface
  rejection. Superwhisper's native diagnostic exposed the separate GL limit
  failure (15 inter-stage shader variables requested, 14 allowed), followed by
  a zink software retry with no CPU Vulkan device and terminal fallback.
- Explicit llvmpipe rendered DiskTree's private one-file directory and the
  Superwhisper configuration GUI. Screenshots were inspected, not merely
  process liveness/window mapping. Herdr's existing CPU GL path was retained.
- After installing user-local desktop overrides, Gio resolved both normal
  desktop IDs to `~/.local/share/applications/`. Actual GIO launches produced
  owned native Wayland DiskTree and Superwhisper windows; final pixel captures
  showed the file tile/detail controls and graphical configuration controls.
  No whole-disk scan, file removal, settings change or dictation was exercised.
- Final process inspection retained the original Herdr GUI/core and
  Superwhisper daemon/ASR/vocabulary worker PIDs and start times. Compositor
  zink/private Vulkan selection remained intact. Only owned diagnostic GUIs
  were closed. The workaround is CPU GL, not general native GPU qualification.

## Isolated native GPUI minimum-limit qualification - 2026-11-15

- Acquired DiskTree 0.11.0 at immutable commit
  `6c8d4ce6211bf135ff4e42890faebff1040e1846` and verified the locked GPUI-pre-wgpu
  0.3.6 archive SHA256. Installed checksum-verified native Rust/Cargo 1.97.0 in
  a private prefix; no host toolchain/package or installed app was replaced.
- Cargo prerequisite failures occurred before test execution: local renderer
  workspace membership, missing published-crate font fixtures and test-support
  exports. Added the real prerequisites, including font/license Git-blob checks
  against the exact Zed revision. Dependency versions/sources/checksums stayed
  unchanged. No tests were deleted, disabled or replaced by dummy fixtures.
- The exact `native_shader_interfaces_fit_requested_limits` regression passed:
  one test, zero failures. The isolated native ARM64 DiskTree executable built
  with locked dependencies; SHA256
  `ce44c7449bcefc11f3bb030b0872c69d07db3f2a317a49c02d272a5e4b23fc3b`.
- Actual llvmpipe-reference and native-zink runs used the same executable and
  private four-file input. Native telemetry identified M5 Pro G17S Borealis,
  IntegratedGpu, GL backend, six requested inter-stage variables. Both captured
  native Wayland windows were 1238x2122 and visually inspected.
- Whole-frame comparison: PSNR 63.2192 dB, normalized RMSE 0.0006903; not
  bit-identical. Native treemap, text, controls and hatching rendered correctly;
  the native kernel journal window had no entries. Both owned GUIs were closed.
- Preserved installed software launchers, global Mesa selection, kernel/boot
  and active user services. Native Vulkan admission was not relaxed. This is
  limited DiskTree hardware rendering, not other apps or sustained stability.

