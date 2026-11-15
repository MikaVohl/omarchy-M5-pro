# Hardware qualification

This is a source release of a working bring-up, not a supported installer or a daily-driver certification. Persistent Omarchy boot, owner setup, and an authenticated native Hyprland session were observed. The final display mode survived a restart. Newly rebuilt artifacts must be qualified on their own target before disk deployment.

| Component | Observed result | Boundary |
| --- | --- | --- |
| Board | Mac17,8 / J716s / T6050 M5 Pro | Not J714s, K114c, M5 Max, or a generic Apple Silicon recipe |
| Firmware | macOS 27.3 beta 1, build 26C5035r; mBoot-20457.60.65.0.1 | Exact running macOS, system firmware, and stub pairing required |
| Kernel | 7.3.0-j716s-8ab10100a4c1; all 18 CPUs booted | Pinned source; newly generated module signing keys change build bytes |
| Root | Persistent Arch Linux ARM + Omarchy | Full initial image write/readback passed |
| Owner setup | Physical tty1 account/password; authenticated SDDM login | No default credentials, forced SSH, or passwordless sudo |
| GPU | Native G17S vertex/fragment work and native Vulkan/zink session; one scheduling-timeout recovery observed | No complete compute/API conformance or clean stability claim |
| Panel | Native DCP, full-notch 3456×2234 at 120 Hz | Hyprland configuration verified; external outputs not qualified |
| Keyboard/trackpad | Physical interaction verified | Board-matched trackpad firmware required |
| Network | Wi-Fi association worked | No Wi-Fi performance, roaming, or Bluetooth qualification |
| Bluetooth | Connected headset/A2DP and controls observed; exact native standard-SCO transport candidate built | New SCO kernel not booted; microphone qualification pending; private HDR-SCO unsupported |
| USB-C / USB4 | Left-front/right Type-C, DWC3 and PHY bind; right ACIO binds | Unplugged device role intentionally defers xHCI; no physical attach/PD/DP/tunnel qualification |
| Video decode / Moonlight | 48 H.264 frames matched software; real 1080p/60 hardware H.264 Desktop stream reported good | Four-minute smoke, not a long soak; HEVC pixel correctness, AV1, VP9 and higher resolutions unqualified |
| Video encode / ANE | Native ProRes APRI prepared and DMA quarantine candidate built; AVE command sizes and ANE preload geometry differ from the pin | No encoder/ANE startup; native quiescence/descriptor/boot contracts unresolved; acquisition guards remain |
| Battery / sensors | Battery/cpufreq/WFI telemetry; native illuminance calibration requires 193 bytes | Same-unit HmCA is 9,962 bytes, not directly usable illuminance calibration; ALS operation and TTPD semantics unqualified |
| Audio | Same-unit AID41 blob compiled; six-amp/twelve-sense silent wiring and fail-closed interlock checks passed | Native 26C5035r DSP parity missing; normal speaker playback remains disabled |
| Suspend/resume | Not qualified | Do not infer safety from a working desktop |
| External displays | Not qualified | Internal panel success does not qualify external DCP paths |
| Camera | Exact-build 28,672-byte tuning candidate prepared; current ISP handoff inspected | Not installed; sensor identity, firmware ABI, privacy and capture unqualified |
| Touch ID / SEP | `touchid` kernel running; finger enrolled; successful Quickshell fingerprint PAM authentication and unlock observed | Sudo/polkit configured, not separately exercised; SDDM remains password-based; reboot matching and shared-enclave durability unqualified |
| Stability / repeated recovery | Not qualified | No broad daily-use or repeated retained-macOS recovery campaign |

The opt-in open-network Wi-Fi kernel
`7.3.0-j716s-8ab10100a4c1-open-wifi` booted successfully. Live inspection
confirmed association to an open AP and successful boot-identity validation;
a working guest-network connection was reported. This qualifies that observed
connection, not all open APs, roaming, performance, or physical WPA2/WPA3
regressions. After the successful trial, the tested payload was selected as the
persistent default; the original baseline and rescue remain preserved. No
subsequent normal-default boot has yet been observed.
See [setup](setup.md#experimental-open-network-wi-fi-candidate).

Obtain and verify a current backup before partitioning or deployment.

## Bringup improvements — 2026-11-14

The factory-data results below include session
`2026-11-15T03-50-42-200Z_01a1dd54-2718-7505-9803-008623374b39`,
whose recorded checks are dated 2026-11-14. Its source and detailed records
were already present when integrated; no hardware experiment was repeated.
Preparation removed missing-data blockers, not hardware safety or ABI gates.
See [verification](verification.md#factory-data-session-integration) for the
executed checks and remaining qualification boundaries.

The live WirePlumber fallback sink used the speaker port but did not contain
`Speakers` in its name. The repo and installed policy now disable every
`alsa_output.platform-sound.*` output except a dedicated UCM `Headphones` node.
After restarting WirePlumber, no internal playback sink remained; microphone
inputs were preserved. Generic fallback headphone output is deliberately not
exempt because it shares a speaker route. USB/Bluetooth output is unaffected.

The new `7.3.0-j716s-8ab10100a4c1-bringup` candidate combines the observed open
Wi-Fi change, built-in Bluetooth profile/input capabilities and diagnostic-only
G17 recovery snapshots. Its artifacts and installed selection passed hash-bound
checks and it was authorized as the persistent default without rebooting.
At activation, the running kernel was still the open-WiFi trial. Previous
default/runtime bytes, baseline root and rescue are retained. Ordinary bringup
boot is now observed below; Bluetooth profiles and failed-boot fallback remain
physically unqualified.

At 15:28:45 the running GPU firmware reported reason 5 (scheduling pause timeout),
no blamed queue, no MMU fault and four retained Hyprland commands. The cause is
not established. The candidate improves halted-state diagnostics without changing
timeouts, attribution, scheduling or retirement. Private evidence capture now
normalizes boot UUIDs and timezone-qualified incident windows for journalctl.

The matching open AVD v5/tier2 firmware was built reproducibly, installed with a
rollback receipt, then loaded under explicit attended authorization. Firmware
boot reported hardware version `40020` and seven queued frames. VA-API H.264
decode produced 48 frames bit-identical to software. Moonlight 6.2.0 initialized
VA-API, selected the native Vulkan renderer and completed its HEVC Main10 probe.
Its app-scoped desktop entry selects `v4l2_request`; no global graphics driver
override or browser sandbox weakening was added.

AVE's current 26C5035r image does not match the pinned driver's admitted build
and TEXT size. ANE handoff exists, but its preload ranges do not match the pinned
profile; the logged zero hash is not a measured firmware hash. ProRes initially
lacked validated T6050 register rows; exact-build native extraction is recorded
below, without engine qualification. Camera still requires a J716s/26C5035r
handoff and sensor data; copying another board's firmware is not a solution.
These guards remain.

USB, PD, external displays, charging and suspend need attended physical tests.
The read-only campaign helper records both cable orientations, before/after
evidence and remaining errors without awarding a physical pass. WFI startup
explicitly ignores `idle=nop`; keep-on clock/domain flags remain unchanged.
ALS preparation requires factory `CalibrationData` captured from this same
physical unit in the exact paired macOS build. No calibration was invented.

## Ordinary bringup boot — 2026-11-14

The owner restarted into `7.3.0-j716s-8ab10100a4c1-bringup`. The boot-binding
service completed successfully, and no `chosen/aurora,trial` property is present:
this is the persistent default boot, not a one-shot trial. No failed system or
user services and no Hyprland config errors were reported.

The panel remains 3456×2234 at 120 Hz. GPU preflight identifies G17S/chip
`0x6050`, native Borealis Vulkan and DCP scanout; the compositor retains its
private Mesa/zink environment. Wi-Fi associated to the open 5 GHz AP and carries
bidirectional traffic. Keyboard and trackpad enumerate in Hyprland.

AVD loaded automatically as `/dev/video12` with `/dev/media0`. A fresh two-second
320×180/24 Hz H.264 clip decoded through VA-API; all 48 frame checksums matched
software. No GPU recovery, AVD timeout/error, Oops or SError entries were found
in this early boot after the check. This is not a long stability qualification.

RFCOMM, LE, BNEP and uinput are enabled in the running config; an unprivileged
RFCOMM protocol socket opens successfully. The Bluetooth controller is powered,
but the paired headset is disconnected and BlueZ reports host down. Actual
headset playback, microphone, media controls and LE/PAN sessions remain untested.
Speaker guarding persists: no internal playback sink; microphone inputs remain.

The separate privileged manual guard/BNEP socket command timed out without
output. Boot-binding success is established by the actual boot service; no
privileged BNEP socket claim is made. ALS calibration, AVE ABI, camera, ProRes,
ANE, physical port/charging and suspend limitations remain unchanged. SEP-probe
was not selected or tested.

## Attended headset and streaming checks — 2026-11-14

The existing headset pairing connected and services resolved. A2DP SBC-XQ
playback was submitted only to the Bluetooth sink; the owner confirmed hearing
the louder test audio well. The owner pressed play/pause and volume and observed
Omarchy's play/pause toast. The bounded AVRCP event reader captured no events in
its 25-second window, so no device-level event capture or volume-change pass is
claimed from that reader.

The HFP mSBC profile switched successfully, but microphone recording/playback
stalled and PipeWire reported Bluetooth audio-transport errors. The recording
was deleted and stereo mode restored. The pinned `btcentauri_t6050.c`
`cen_hci_send()` accepts HCI command and ACL packets and rejects other types
with `-EOPNOTSUPP`; its receive path likewise has no SCO delivery. RFCOMM
availability does not implement headset voice transport. No microphone pass or
speculative ring/protocol fix is claimed. Reconnection restored an active
stereo sink after a transient A2DP endpoint-in-use failure.

Moonlight resumed the already running Sunshine Desktop on the authorized host
at 1920×1080/60, 20 Mbit/s, hardware H.264 forced. Logs confirmed real video
packets, VA-API decode through AVD and the EGL/GLES renderer with VA-API backend.
The owner reported a good desktop stream. The client ran for 4m38s before it was
closed without issuing a host-app quit. No GPU recovery, AVD timeout/error,
Oops or SError appeared during the inspected interval. This is a limited
streaming smoke, not a long-session, other-codec or all-resolution qualification.

One ordinary bringup boot is observed. Repeated ordinary boots and the separate
SEP cold-boot experiment still require the owner's saved-work and recovery
workflow. The SEP candidate remains staged and unarmed at this checkpoint.

## Touch ID investigation — 2026-11-14

On `7.3.0-j716s-8ab10100a4c1-open-wifi`, a temporary load of the installed
`apple_sep` module with `xart_writes=0 provision_keybag=0 xart_guard=2
trusted_keys=0 filevault_api=0 keystore_only=1` logged
`no platform profile for this SoC; the SEP stays disabled`. No SEP device nodes
appeared, and the unbound module was unloaded. This tested admission only:
no SEP mailbox registration, sensor session, enrollment, or match occurred.

Read-only inspection of the live ADT confirmed a booted SEP, Mesa sensor
`0x3356` on SPI2 at `0x505108000`, 8 MHz, power on NUB GPIO 21 and data-ready
on AP GPIO 138. SEP DART stream 0 uses `0x10000004000 + 0xffff0000`.
The Linux tree leaves SEP, its mailbox and SPI2 disabled, has no sensor child
or SEP alias, and lacks SEP firmware reservations and boot manifests.
The stage-2 log records `FDT: sep alias not found in devtree`.

The APFS metadata inspector validated one unshared 6 MiB gigalocker extent,
no snapshots or pending revert, and valid live-record CRCs. A read-only scan
found one distinct FSC2 calibration. The factory-cache reader found FSC2,
MSRk, sealing and trust records, but current-boot identity admission cannot
complete without the forwarded SEP policy manifest. No factory data was
installed or exported, and no enclave-storage writes were authorized.

A throwaway host harness exercised the production Aura SEP handoff against
the actual candidate J716s DTB, including its two `soc` paths. Correct alias
resolution, firmware reservation and 2893/9025-byte fixture-manifest attachment
passed while SEP stayed disabled. Synthetic manifest contents make this a
host-only check, not proof that a SEP-enabled payload boots.

The pinned kernel's T6050 profile selects J714s, not J716s, and inherits a
`0x40000` registration table. Earlier J716s preparation under
`work/sources/bringup/contrib/pending/platform/touchid-sep/` instead records
a macOS 27.3 hypervisor capture with a `0x1c000` table and seven initial
endpoints; its table contents and later biometric discovery were not captured.
Do not broaden the J714s selector or enable enrollment by removing a guard.
A board-specific, separately booted discovery-only candidate is the next
hardware experiment; preserve the working default and rescue.

### Prepared discovery-only candidate

Kernel compilation and module signing completed for
`7.3.0-j716s-8ab10100a4c1-sep-probe`. Packaging initially failed because
`fdtput` resolved the ambiguous `/soc` path to its unit-addressed sibling;
exact-name libfdt traversal repaired the compiled artifacts without a rebuild.
Staging then correctly rejected the historical all-six-disabled KIS recipe:
the installed baseline disables USB0's three nodes but keeps USB2's three
enabled. Packaging now preserves those exact baseline states.

The non-arming staging command passed receipt/module checks and exact DT-delta
validation, installed separate `aurora/sep-probe` files, and left the default,
rescue, dependencies, guard and spent trial marker unchanged. The only DT
changes are the SEP alias, mailbox enablement and always-on SEP power supplier;
SPI2 and the sensor remain disabled. The candidate refuses unsafe module
parameters and the `complete_attach` transition. Sixty-two authorization tests
and three exact-DT/baseline-state regressions passed. At staging, the candidate
had not booted or attached SEP; the subsequent cold-boot discovery is recorded
below. Enrollment and matching remain outside the authorization.

Experimental precedents include M4 Pro J616s
([reboot matching](https://github.com/aurora-silicon/linux/pull/545)), M4 Max
J616c ([enrollment and reboot matching](https://github.com/aurora-silicon/linux/pull/732)),
and M4 Air J713 ([enrollment, matching and reboot matching](https://github.com/aurora-silicon/linux/pull/457)).
These contributor reports do not qualify J716s or its macOS 27.3 SEP firmware.
[Official Asahi M5 support](https://asahilinux.org/docs/platform/feature-support/m5/)
still lists SEP and Touch ID as TBA.

## Why the display configuration differs from a stock setup

The T6050 kernel uses the G17S backend. `DRM_ASAHI_M5` governs the separate T8142 backend; switching it on does not select the T6050 GPU.

The private Mesa prefix is `/opt/mesa-j716s`, built from the pinned Aurora Mesa source without replacing its UAPI header. The recorded kernel header adds optional compute flags, not changed structures or ioctl identities; the exact comparison is retained under `outputs/persistent-install/graphics/`.

SDDM filters its daemon environment. Its greeter needs the private graphics variables through `[General] GreeterEnvironment`; an ordinary systemd `Environment=` override did not work.

Full-notch mode needs both `appledrm.show_notch=1` on the kernel command line and `chosen.asahi,show-notch=1` in the Aura payload. Neither flag supplies a different board's display data.

## SEP one-shot arming — 2026-11-14

After the local source checkpoint and attended playback/streaming checks, the
owner chose the separate SEP cold-boot experiment and explicitly waived the
recommended backup. Physical recovery access and saved work were confirmed.
Dual-boot macOS was explained as a recovery route sharing the SSD/SEP, not an
independent backup. The private receipt records `waived-by-owner`.

`scripts/stage-sep-probe.py --arm --acknowledge-risk --attended-cold-boot
--waive-backup` passed the live boot guard, staged receipt/module checks, exact
DT delta, unchanged default/rescue/dependency checks and direct-disk marker
readback. The next Linux start is armed once for
`7.3.0-j716s-8ab10100a4c1-sep-probe`; persistent bringup selection is unchanged.
At arming, no shutdown/reboot, SEP registration, sensor access, enrollment or
enclave write had been performed. The subsequent trial boot and discovery are
recorded below. Arming alone is not evidence of Touch ID support.


## Observed SEP discovery — 2026-11-14

The owner returned from the attended start running the exact `sep-probe` release,
with `chosen/aurora,trial=aurora/sep-probe.bin`, all six safe parameters and a
successful boot-binding service. The loader forwarded SEP firmware reservation,
the 9,025-byte iBoot manifest and 2,898-byte local-policy manifest. The one-shot
marker was spent and the persistent bringup default remained unchanged.

The first explicit modprobe repeated the safe parameters already supplied by
kmod from the kernel command line. Rust's `SetOnce` parameter storage rejected
the duplicate assignments with `EEXIST`; kmod returned success even though no
module loaded or driver initialization occurred. The rejected attempt was kept
privately. A corrected dry-run showed each safe option exactly once; loading
with `modprobe apple_sep` then performed the single actual discovery attempt.

The J716s safety profile and `0x1c000` capacity were admitted. SEP/DART enabled
and the platform driver bound. The driver read the APFS-resolved xART extent
with 170 slots and 12 live records, reporting zero malformed, duplicate or
repaired entries and writes disabled. It saw seven initial endpoints in 18
messages, then advertised 12 endpoints (`cntl sbio scrd sse hdcp xars sks xarm
hibe pnon skdl stac`) and reported ready in 1,442 ms with key store open.
The ready-phase survey registered hwrng on attempt 1.

This establishes the observed transport/discovery bootstrap only. The existing
key-store open is part of keystore-only bootstrap; no manual keybag, FileVault,
sensor, complete-attach, enrollment or persistence operation was issued. There
are no biometric device nodes; the SPI sensor remains disabled. No failed
system/user services were reported. Private attempt/results/journals remain
under `/var/lib/j716s/sep-discovery/<boot-id>/`, excluded from source publication.

Do not unload/reload or rearm to repeat registration. Use a full power cycle
before another experiment. Linux Touch ID enrollment/matching and persistence
remain unqualified; the discovery-only candidate is not promoted into the
permanent bringup kernel.


### Owner-observed macOS return

After the SEP discovery experiment, the owner reported that macOS and Touch ID
work normally. This records one successful owner-observed return, not an
independent biometric/storage audit or repeated recovery campaign. It does not
qualify Linux sensor authentication, enrollment, matching or persistent writes.

### Second discovery boot and sensor material — 2026-11-14

A second armed cold boot selected `-sep-probe` without loading `apple_sep`. Aura
forwarded `local-policy-manifest` (2,898 bytes) and `iboot-manifest` (9,025
bytes) under the SEP node; no failed services. On that boot the same-unit factory
package was prepared read-only from the iBootSystemContainer. The unit factory
seal is an IM4M **version 2** (10,989 bytes); the local policy and both original
FSC2/MSRk image manifests are version 0. Preparation now admits version 2 only
for the unit seal. The seal binds board, unit serial and both payload digests.
The resulting containers are 133,821 (FSC2) and 14,242 (MSRk) bytes; Apple
signature validity is untested until the enclave sees them.

The `sep-sensor` candidate was then staged unarmed. The ESP needed room: an
unreferenced 203 MB `aurora/desktop-probe-20261113.tar.gz` was moved, hash-verified,
to `/var/lib/j716s/esp-archive/`. No sensor operation has run.

### Sensor authentication trial — 2026-11-14

The armed cold boot selected `7.3.0-j716s-8ab10100a4c1-sep-sensor` with exactly
one copy of each safe SEP parameter plus `apple_sep.sensor_auth_probe=1`. Before
loading, `spi0.0` (`spi:mesa-fingerprint`) existed unbound. One `modprobe apple_sep`:

- Safety mode logged as bounded identity/authentication only; xART writes,
  provisioning, trusted keys, FileVault, capture, templates and persistence
  disabled; `complete_attach` forbidden.
- Same xART view as discovery (170 slots, 12 live records, writes disabled).
- `apple-mesa spi0.0` bound at 8 MHz, SPI mode 2, 20 ns CS timing; the sensor
  power line came from the device node.
- `sbio` reported a changed biometric device list, then
  `sensor_auth: stage=17 errno=0 sep_status=0x00000000 result=session-accepted`;
  the driver powered the sensor off afterwards.
- Ready in 1,453 ms with the same 12 endpoints; key store open; hwrng
  registered; `matching: blocked`; `xart: disabled`; no failed services.

The diag `policy` line reports `xart_guard=0` while the kernel safety line says
guard 2. With `xart_writes=0` no xART write path runs at all, so the guard has
nothing to filter. This reporting difference has not been explained further.

So the enclave accepted one authenticated session with this unit's sensor while
the prepared same-unit material was installed. Which material the session used
is not separately observed. The sensor patch upload,
calibration, enrollment, matching, PAM integration and any persistence remain
unimplemented and unqualified. Private evidence is under
`/var/lib/j716s/sep-sensor/<boot-id>/`.

After the trial the owner booted macOS and reported Touch ID unlock and matching
working normally. The next Linux start returned to the bringup default with the
marker spent and boot-guard checks passing. This is one owner-observed return,
not an independent enclave-state audit.


## Speaker factory tuning recovered — 2026-11-14

On `7.3.0-j716s-8ab10100a4c1-touchid`, the J716 macaudio card and all six
SN012776 controls exist. The pinned board configuration caps both safe and
unlocked output at -30 dB. The live kernel still exposes the locked maximum of
1 on every speaker-volume control, no internal PipeWire playback sink and a
masked/inactive `speakersafetyd`. Amp gain reads 15; the old provisional
gain-10 experiment is not the current hardware configuration.

The retained macOS System and Preboot volumes were mounted **read-only** using
an isolated, patched APFS reader, without opening encrypted Data or supplying
FileVault credentials. SystemVersion.plist confirmed `26C5035r`. The pinned
[Aurora collector](https://github.com/aurora-silicon/aurora-installer/tree/e8f0711f8f77cc5fdaf3f78348321d865a3818bc/tools/speaker-tuning)
recovered 35 AID41 source files (41 bundle files), speaker vendor 3, the live
ADT's 138-byte six-speaker factory calibration, the AudioDSP lattice and
BuzzKill matrix. Board/build/vendor and same-unit serial-hash binding passed.
The serial number and Apple data are not published.

`scripts/prepare-speaker-tuning.py` reproduced the collection into a new private
package and verified every upstream manifest record and the local receipt.
Four targeted regressions passed, including foreign-unit, vendor/build,
missing-calibration and writable-System refusal. The APFS patch applies without
fuzz; its compiled reader actually read the retained System/Preboot. Both mounts
were unmounted after material preparation.

This removes the **missing factory data** blocker, not the speaker safety gate.
The old J716 profile explicitly copies unmeasured J316 thermal parameters; it
was not installed. The new protection stack's
[v2 README](https://github.com/aurora-silicon/speakerguardd/blob/v2/README.md)
marks it alpha/not-for-use. Its default `04169c4` reader refused the collector's
new `buzzkill_quadrature` field; metadata was not stripped to force acceptance.
The schema-aware `54c911c` reader then validated the unmodified bundle and
compiled a private 1,668-byte Spp3 protection blob: six output channels, twelve
sense channels, 48 kHz, J716s binding and same-unit calibration in use. It
reported **26C5035r is not a validated build (reference: 25G83)** and requires
the differential rig. This is offline compiler proof, not a runtime protection
or playback pass. The blob is not installed.
No daemon was started or unmasked, no speaker PCM was opened and no boot payload
or audio policy was changed. Safe enablement still needs compatible protection
code, exact sense mapping/scaling, DSP routing and dead-daemon interlock proof.


## Camera and ALS material preparation — 2026-11-14

The retained exact-build System daemon supplied a byte-identical 28,672-byte
`isp_2224_01XX.dat` candidate from all three universal-binary copies.
`scripts/prepare-camera-firmware.py` pins the extractor and daemon bytes,
records current-boot ADT provenance and refuses an externally unanchored or
changed package. Seven isolated camera regressions and a live package check
passed. The private package remains under ignored `outputs/firmware/`.

The live ISP is disabled, preloaded and exclave-assigned (SIDs 4/5/8/9).
Its current TEXT/DATA physical addresses differ from the old static camera
overlay; that overlay must not be deployed. Platform ID 118 does not establish
IMX958 identity. Linux still lacks the firmware memory-region handoff, and
firmware ABI, sensor identity, privacy indicator and coprocessor quiescence
remain unqualified. No camera was started or firmware installed.

Read-only ISC Hardware-cache recovery found one genuine same-unit HmCA IMG4
record (12,376 bytes; payload 9,962 bytes), preserved with its unit seal.
**HmCA is not yet proven equivalent to ALS IORegistry `CalibrationData`.**
No conversion was invented and it was not installed as `aop-als-cal.bin`.
The live ALS ADT contains no calibration. Existing exact-unit macOS IORegistry
capture remains the supported source for installable calibration.

The ALS tool now has hash-anchored dry-run/install/check/rollback operations
for a genuine prepared CalibrationData package. Boot/firmware identity is
required and rollback binds both bytes and the published inode. Eighteen
targeted port/ALS tests passed. No ALS calibration was installed, no AOP
activation was issued, and the missing-calibration hardware limitation remains.

Bluetooth gained a passive negotiation/SCO-header evidence collector with a
locked kernel BPF privacy filter; ACL/ISO/vendor/key packets and SCO audio
payloads are excluded before userspace. Eighteen regressions passed, including
real Linux socket-filter execution on synthetic datagrams. A one-second idle
HCI monitor capture completed with zero control records and no Bluetooth state
changes. This verifies the tool only, not the headset mic. Transport SCO TX/RX
and exact-firmware ring framing/credits remain missing; no speculative rings
were enabled and the failed microphone experiment was not repeated.


## Exact-build encoder and neural-engine progress — 2026-11-14

Retained 26C5035r Preboot supplied genuine AppleProResHW 627 tables selected by
T6050's native device-ID paths. `scripts/prepare-prores-firmware.py` generated
a private 2,880-byte APRI candidate with 82/80/62/12 register rows; its control
block preserves eleven CED rows and translates the native SSW memory-index-4
row to `ctrl+0x1000`. Source and decoded-image hashes, native pointer encoding,
terminators, row bounds and final candidate bytes are pinned. No foreign-chip
tables or arbitrary row-map input were used. The offline package check passed;
the image is **not installed and ProRes has not started**.

AVE's actual image is `AppleAVE2FW-9013.55.1.release`: TEXT `0x158000`, DATA
`0x134000`. The file TEXT CRC matches the live loader CRC. The pinned driver
[qualifies 26A428](https://github.com/aurora-silicon/linux/commit/55b7401a4b03bbedb487aae09770bafc0a17cefd)
and expects TEXT `0x154000`; the 26C5035r image is a different firmware
generation, not an absent file. Changing just the size/build allowlist would
not establish command/protocol compatibility. AVE's `-EINVAL` guard remains.

ANE **is handed over by the loader**: current preload TEXT/DATA sizes are
`0xec000`/`0x280000`, with DATA IOVA offset `0xec000`. The pinned profile
requires `0xc0000`/`0x2ac000`, with offset `0xc0000`. Current code does not hash
the rejected layout, then misleadingly logs a zero digest as a TEXT-pin
failure. A retained-file TEXT digest was measured privately; it is not a
live-preload measurement or evidence that the engine can safely start.

`patches/kernel-j716s-media-handoff-diagnostics.patch` is an isolated,
**undeployed** candidate. It improves AVE actual/expected-size diagnostics and
ANE refusal reasons, hashes ANE TEXT only after existing no-map/outside-RAM
reservation checks, and distinguishes measured/unmeasured hashes. Layout,
TEXT-pin and cold-handoff acquisition checks remain. Observation follows
[upstream ANE work](https://github.com/aurora-silicon/linux/commit/79e1e9f88449f2ad8a9a97648f91df8c656157d2)
without broadening admission. Sixteen offline media tests and zero-fuzz patch
application passed. A throwaway host compilation executed the production
KUnit handoff fixture (13 assertions), including refusal of the exact new
geometry even with `text_ok=true`; this is not a full kernel build/KUnit boot.
No encoder/ANE startup, module load, boot arming or default selection occurred.

## Enablement checkpoint — 2026-11-14

The running release is `7.3.0-j716s-8ab10100a4c1-touchid`, with platform state
`ready` and `libfprint-git 2:1.94.100.aurora.a227a35-1`. fprintd lists an
enrolled right index finger. The existing 22:17:24–22:17:26 journal shows the
Quickshell session becoming secure, starting PAM with
`omarchy-lock-fingerprint`, prompting for the enrolled finger, authenticating
successfully and unlocking. This qualifies **basic enrollment, matching and
lock-screen fingerprint unlock**, superseding the sensor-authentication-only
checkpoint above. It does not qualify SDDM biometric login.

Sudo and polkit contain fingerprint authentication followed by password fallback
and the lid-closed skip gate. Quickshell reports fingerprint available and
password PAM present. Those readiness checks do not independently establish
sudo/polkit authentication or password-fallback execution. A separate bounded
`fprintd-verify` invocation reached verification but timed out after 45 seconds
without a match result; it is not counted as a second successful check.

No new kernel was selected during this checkpoint. The current Touch ID kernel
is already running; the userspace library replacement does not require another
kernel reboot. Reboot matching, repeated authentication and retained-macOS
Touch ID after Linux enrollment remain separate qualification work. Linux has
now used shared enclave storage; preserve the previously documented risk.

**Basic bringup complete within the observed scope:** persistent desktop boot,
internal display, accelerated rendering, keyboard/trackpad, the tested open
Wi-Fi connection, Bluetooth A2DP, short hardware H.264 streaming, and Touch ID
lock-screen unlock. These are not daily-driver certification or complete API,
codec, security-mode and recovery coverage.

**Remaining enablement:** speaker protection/parity and hardware interlock;
ProRes installation and engine qualification; 26C5035r AVE/ANE compatibility;
camera handoff/identity/privacy/capture; genuine ALS CalibrationData; Centauri
SCO transport. USB-C/PD/charging/external displays and suspend still need
attended physical qualification. Prepared files alone do not complete these
features; speakers remain guarded and no unqualified engine was started.

## Speaker-priority bringup — 2026-11-14

Root can read `Speaker Protection` and `Speaker Protection Interface` on the
running `touchid` kernel. Their absence in an unprivileged mixer listing was
not a missing kernel interface. The selector initially served the legacy
volume lock; protection was not granted.

`speakerguardd-j716s-transport.patch` adds only the native T6050 transport
description to the pinned schema-compatible protection source. The kernel's
`HW_CFG_T6050` specifies six S24_LE playback slots and twelve S16_LE sense
slots, DMAengine PCMs and firmware-owned serializer clocks. IRQ scheduling
is not enabled without cadence qualification. Live DT/tuning derivation
produced card `AppleJ716`, Spp3, physical/unit map 0–5 and V/I ports 0–11.

The upstream silent self-check then passed on hardware with a single 50 ms
lease and continuous capture armed before **zero-only** playback. It checked
amplifier registers, sense switches and format, bound wiring
`cbba18ec53f0a442` to this kernel/blob, and released its grant. A separate
zero-only smoke stopped renewals, then killed the owner in another case:

| Case | Observed withdrawal and playback stop | Final speaker PCM |
| --- | --- | --- |
| Lease expiry | 53.0 ms | SETUP |
| Owner process death | 76.5 ms | Closed |

These are observed timings including userspace detection, not worst-case
latency certification under load. The original interface and audio services
were restored; the disposable smoke script was removed. No normal playback
sink, unmasked protection daemon or nonzero speaker sample was introduced.
The private self-check is bound to `7.3.0-j716s-8ab10100a4c1-touchid`; a
different kernel needs a fresh check, not inherited qualification.

The same-unit 26C5035r AudioDSP executable differs from every recorded older
reference hash. The voicing check passes compiler completeness, finite
output, silence, limiter ceiling and reset determinism; its locked 512-frame
quantum is required. Native macOS parity is still refused. The protection
project remains alpha; neither the silent pass nor the new transport entry
bypasses DSP/controller qualification.

`capture-speaker-reference.py` prepared a real private, hash-anchored AID41
request with the same-unit tuning and a deterministic 24.15-second program.
Its paired-macOS mode compiles the native offline AudioUnit host and renders
the selected speaker strip; Linux comparison uses the upstream per-AID
attestation rig. It rejects foreign/stale/tampered references, modelled
AudioUnits, non-finite output and incomplete audio. This first rig covers
**full-volume voicing only**, not controller-state parity, a volume sweep,
acoustic quality or authorization to play. Native capture has not run.

### Exact native source findings

Retained System, Preboot and its plaintext APFS Cryptex OS were opened
read-only; System/Cryptex identify 26C5035r. Encrypted Data, FileVault
credentials and Apple security policy were not accessed. Private copies and
source hashes were retained; proprietary executables/calibration are excluded
from publication.

The native Centauri profile and executable/cache disassembly establish SCO
TX/RX rings 3/9, nine slots, 724-byte in-place footers and tagged completions.
TX carries exactly standard HCI header plus payload. RX validates metadata
attributes and preserves packet-status bits. The new transport validates
ownership before reuse, retains queued data on ring-full and fails closed on
bad completions. HCI credit events remain unchanged; private four-byte HDR
mode is unsupported. Hardware voice is still unqualified.

ProRes now has an isolated depth-one quarantine repair: uncertain completion
retains buffers/mappings, scheduler/context and PM, and never resets shared
SCODEC/APR/DART or retries. Late DONE cannot release quarantine; system sleep
is refused while active. STREAMOFF/close may intentionally block until machine
recovery. Native quiescence and descriptor-generation admission remain
unqualified; no first DMA or APRI installation occurred.

Native AVE Process AVC/HEVC sizes are `0x6e08`/`0x78c8`, each 16 bytes larger
than the pin. Equal total ANE preload size does not prove DATA relocation or
patchbay compatibility. Native camera platform 118 appears in both tables,
but does not prove sensor identity, AP/exclave takeover, ABI or privacy.
Native ALS illuminance loading copies exactly 193 bytes to property `0x0b`;
the separate color path uses `0xfb`. Direct HmCA installation is refused.
Paired-macOS capture tools collect the missing evidence without opening a
camera session or inventing calibration.

### Linux candidate selection

The complete `7.3.0-j716s-8ab10100a4c1-touchid-sco` build passed artifact
checksums and target preflight, including exact sensor DT delta and the
unchanged Bluetooth-only config delta. Under explicit owner authorization,
deployment selected it as the next persistent Linux default. Publication and
boot-guard checks passed. Previous Touch ID payload/helper bytes and modules,
baseline/rescue and the trial marker were preserved. No reboot was issued;
the running kernel remains `touchid`. The new kernel's Bluetooth, graphics,
Touch ID and recovery behavior must be checked after the attended return.

The owner chose paired-macOS offline captures after this Linux checkpoint.
Private transfer inputs are encrypted locally; neither the transfer key nor
firmware/calibration/reference data belongs in GitHub. Speaker playback,
headset microphone, encoders/ANE, camera and ALS are not marked finished.

