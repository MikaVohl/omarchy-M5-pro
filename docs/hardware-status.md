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
| Bluetooth | Connected headset, audible A2DP playback and user-observed play/pause toast; RFCOMM/BNEP sockets work | HFP mic test failed: pinned transport lacks SCO packet support; LE/PAN traffic and prolonged reconnect remain unqualified |
| USB-C / USB4 | Left-front/right Type-C, DWC3 and PHY bind; right ACIO binds | Unplugged device role intentionally defers xHCI; no physical attach/PD/DP/tunnel qualification |
| Video decode / Moonlight | 48 H.264 frames matched software; real 1080p/60 hardware H.264 Desktop stream reported good | Four-minute smoke, not a long soak; HEVC pixel correctness, AV1, VP9 and higher resolutions unqualified |
| Video encode / ANE | AVE build/segment mismatch; ANE preload layout mismatch; engines remain guarded | No arbitrary firmware substitution or ABI guard removal |
| Battery / sensors | Battery, cpufreq and WFI telemetry present | ALS needs same-unit calibration; TTPD is invalid-looking finite firmware data with unknown semantics |
| Audio | Not qualified | Speakers intentionally disabled; do not remove the safety policy |
| Suspend/resume | Not qualified | Do not infer safety from a working desktop |
| External displays | Not qualified | Internal panel success does not qualify external DCP paths |
| Camera | Not qualified | No claim of support |
| Touch ID / SEP | Discovery attached (12 endpoints, ready ~1.45 s). One bounded sensor authentication session **accepted** by the enclave on the `sep-sensor` trial | Authentication only: no patch/calibration upload, enrollment, matching, xART writes or persistent biometric support |
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
profile; the logged zero hash is not a measured firmware hash. ProRes needs
validated T6050 register rows. Camera requires a J716s/26C5035r handoff and sensor
data; copying another board's firmware is not a solution. These guards remain.

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

