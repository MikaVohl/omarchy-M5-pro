# Build and installation sequence

Run commands from the repository root. Distinguish workspace builds from target operations: a successful build does not authorize partitioning, enrollment, reboot, or persistent deployment.

## 1. Prerequisites and firmware pairing

- Exact target: Mac17,8 / J716s / T6050 M5 Pro. Do not substitute J714s/K114c firmware, DTBs, panel geometry, or per-unit data.
- A current, verified backup and physical access to Startup Options/paired recovery.
- Retained, bootable macOS and intact Apple ISC/system Recovery. The running macOS, system firmware, restore bundle, and Linux stub must match **27.3 beta 1 / 26C5035r / mBoot-20457.60.65.0.1**.
- Obtain the matching Apple installer through an authorized Apple source. Installing/staging macOS is an owner-controlled prerequisite, not something these workspace build utilities do. An IPSW is a firmware input here, **not a request to erase/restore the Mac**. Do not run a whole-device restore to obtain firmware.
- AC power for all target preparation/deployment. Save work before any controlled boot.
- Host Python 3.12+, Git, HTTPS access, and a working ARM64 OCI runtime. On macOS use Apple's `container`; on Linux use `podman` or `docker` with native ARM64 or separately configured emulation. Guest builds require case-sensitive filesystems.
- Sufficient free build space for kernel/modules, a roughly 27.2 GB IPSW, root-image work trees, and image exports. The root image is 32 GiB before deployment/growth; preparation keeps macOS space and protected partitions separate.
- A Linux KIS receiver is strongly recommended for early-boot evidence. See [console capture](console.md).

A firmware mismatch, unavailable pinned toolchain/package, missing owner authentication, or unqualified rescue is a blocker. Do not bypass guards or silently use a newer version.

## 2. Verify and acquire public sources

```sh
python3 -B scripts/verify-release.py
python3 -B scripts/prepare-sources.py --fetch
python3 -B scripts/prepare-sources.py --check
```

`--fetch` downloads only public source inputs into ignored `work/sources/`, validates archive hashes/immutable Git commits, and verifies extracted content. It does not download an IPSW or touch target volumes. `--check` is read-only and requires an already prepared source workspace. The converter-only Asahi installer source is pinned to `99dff2e968dafcabc2a940865b051e91ffcfafd3` (v0.9.1); unneeded artwork/bootloader submodules are not fetched.

## 3. Build the kernel and Aura

```sh
# macOS host; substitute podman or docker on a configured Linux build host.
CONTAINER_RUNTIME=container bash scripts/build-kernel.sh
CONTAINER_RUNTIME=container bash scripts/build-aura.sh
```

Each launcher creates a disposable Fedora 43 ARM64 guest. Kernel packages select LLVM/clang 21.1.8 and Rust 1.99.1; the kernel's own `tools/aurora-bindgen/build.py` supplies bindgen. The config sequence is `defconfig asahi.config j716s.config j716s-gpu.config`; exports are under `outputs/kernel-candidate/` with `build-manifest.json` and `SHA256SUMS`. A fresh module-signing private key stays guest-only.

Aura applies the exact-26C5035r integration, fixed-clock DCP, and J716s in-memory KIS patches in order. It builds raw Stage 1 and ESP Stage 2 separately, exports `outputs/boot-candidate/loader/stage1/aura.bin` and `stage2/aura.bin`, and records both stages/patches in `loader/build-manifest.json`. These are source-matched fresh outputs, not proof of successful physical boot.

Do not mix kernel/modules, loader stages, or manifests from different builds. Keep generated outputs/receipts locally; none belong in Git.

### Experimental open-network Wi-Fi candidate

The qualified baseline rejects open networks before issuing a join. To build the
opt-in driver experiment without changing that baseline:

```sh
CONTAINER_RUNTIME=docker bash scripts/build-kernel.sh --open-wifi
```

This applies `patches/kernel-j716s-open-wifi.patch` with zero fuzz to the pinned
kernel in the disposable guest. It exports only to
`outputs/kernel-open-wifi-candidate/`, with release
`7.3.0-j716s-8ab10100a4c1-open-wifi`. Its build manifest records the patch SHA-256
and marks the artifacts `unqualified-experimental`. It does not install anything
on the target or change firmware, boot security, partitions, or running Wi-Fi.
On hosts without CPU/memory cgroup controllers, set `BUILD_RESOURCE_LIMITS=0`;
this disables container resource limits, not the kernel's eight-job build limit.
The runtime must otherwise be configured and accessible to the invoking user.

The patch constructs a no-AKM join without a PMK or RSN element, selects an
unprotected BSS, and reports its privacy capability correctly. It does not turn
missing WPA credentials into an open join, allow open 6 GHz, or relax RX checks.
The no-cipher wire selector and omitted security TLVs remain experimental:
firmware enums establish open authentication and no AKM, but are not a captured
native open-network transaction. See [provenance](provenance.md).

Host-only regression checks execute the actual patched join builder, not a
separate reimplementation. On an already patched source tree:

```sh
python3 scripts/test-open-wifi.py --source /path/to/patched/kernel
```

These checks do not contact an AP or prove firmware acceptance, plaintext RX,
DHCP, or internet access. Before testing a candidate kernel, verify a current
backup, retained macOS, and a physically boot-tested rescue with independent
console capture. Preserve the working kernel and complete hash-bound dependency
closure. The existing baseline deployment tool intentionally rejects this
different kernel release; do not bypass its guards or run fresh deployment over
the installed root. The separately reviewed one-shot procedure is documented in
[recovery](recovery.md#one-shot-open-network-kernel-trial); it validates the actual
installed receipts and leaves the configured baseline boot payload untouched.

Use your own configurable 2.4/5 GHz AP. Record association, DHCP, bidirectional
traffic, disconnect/reconnect, then WPA2 and WPA3 regressions with the candidate's
hashes. The driver is built in, so each new candidate needs a controlled boot;
firmware failure may require full shutdown/power-on rather than a warm reboot.
No reinstall, repartitioning, or factory reset is part of this test.

### Bluetooth and GPU-diagnostics bringup candidate

```sh
CONTAINER_RUNTIME=docker BUILD_RESOURCE_LIMITS=0 bash scripts/build-kernel.sh --bringup
sudo python3 -B scripts/deploy-bringup.py
# Only with explicit authorization to select an unbooted experimental kernel:
sudo python3 -B scripts/deploy-bringup.py --activate --acknowledge-unbooted-risk
```

This variant retains the open-WiFi patch, adds built-in RFCOMM, LE, BNEP and
uinput, and adds bounded halted-state G17 diagnostics when firmware names no
blamed queue. It does not fix or suppress the observed scheduling pause timeout.
Exports are isolated under `outputs/kernel-bringup-candidate/`, release
`7.3.0-j716s-8ab10100a4c1-bringup`. The runtime validator pins every patch,
checks the Bluetooth-only config delta against the installed image's config,
and keeps the baseline DTB/initramfs unchanged. The default baseline config
input is the hash-matched `outputs/kernel-open-wifi-candidate/.config`; supply
`--baseline-config` for another copy with those exact bytes.

Activation publishes last, preserves the previous authorized default and runtime
bytes privately, and restores that default on publication/readback failure.
It never changes the running kernel, arms a trial, deletes retained kernels,
repartitions, replaces rescue or reboots. Explicit unbooted activation is a
documented risk exception, not boot/Bluetooth/recovery qualification.
Use [rollback](recovery.md#bringup-candidate-rollback) if needed.

### Open AVD firmware and Moonlight

The installed generic `avd-fw` package lacks the Aurora queue-ABI-2 tier2 image.
Do not rename its tier1 image. Acquire the pinned open-source archive, then build
offline in the disposable guest:

```sh
mkdir -p work/cache
curl --fail --location --output work/cache/avd-fw-ea3a65cc9c0c41907e6ad2714b813e4be6858485.tar.gz \
  https://codeload.github.com/aurora-silicon/avd-fw/tar.gz/ea3a65cc9c0c41907e6ad2714b813e4be6858485
CONTAINER_RUNTIME=docker bash scripts/build-avd-firmware.sh
sudo python3 -B scripts/install-avd-firmware.py
sudo python3 -B scripts/install-avd-firmware.py --install
```

The builder verifies source SHA-256 and pinned AVD driver inputs, builds v5/tier2
twice, checks exact CM3 vectors against ELF `_start`, runs the upstream host queue
test and publishes only identical images. Installation validates the live boot
binding and package, refuses existing firmware/receipts and does not load AVD.
The next probe can boot unqualified hardware; an attended load/decode campaign
requires physical recovery access because a hardware wedge may need a power cycle.
`sudo python3 -B scripts/install-avd-firmware.py --rollback` removes only the
exact firmware this tool installed, without unloading a running decoder.

For a complete IPSW firmware package, add
`--avd-firmware-dir outputs/firmware/j716s-26C5035r-avd` to
`scripts/prepare-firmware.py`. The root-image builder carries the supplied image.
The repo's Moonlight desktop policy selects the separately installed
`v4l2_request` VA-API backend for that app only. Direct CLI equivalent:

```sh
LIBVA_DRIVER_NAME=v4l2_request LIBVA_DRIVERS_PATH=/usr/lib/dri moonlight
```

H.264 hardware frame checksums and Moonlight's startup probe worked on the
observed payload. No streaming host was exercised, and HEVC/AV1/VP9 correctness,
high-resolution throughput, decoder recovery and long-session stability remain
unqualified. AVE/ANE/ProRes/camera firmware guards are not bypassed.

### Read-only port, power and recovery evidence

```sh
python3 -B scripts/qualify-usb-power.py --plan
python3 -B scripts/qualify-usb-power.py --case suspend-readiness --output /private/new-before.json
# After attended attachment of a known device, use the same unit/boot snapshot:
python3 -B scripts/qualify-usb-power.py --case usb3 --port right --orientation 1 \
  --equipment 'known SuperSpeed device and cable' --before /private/new-before.json \
  --output /private/new-after.json
python3 -B scripts/capture-gpu-recovery.py --since '2026-11-14T15:27:30-06:00' \
  --until '2026-11-14T15:30:00-06:00' --output /private/new-gpu-incident
```

These tools never force roles, authorize devices, change charging thresholds,
modify power/clock policy, start GPU workloads, mount debugfs or suspend. Missing
sources/errors are retained; GPU capture may exit nonzero while preserving its
private evidence. Snapshots cannot award a physical qualification pass.

For ALS, capture factory `CalibrationData` on this same unit in its exact paired
macOS build, then validate/stage it on Linux:

```sh
# Paired macOS, read-only IORegistry capture:
python3 scripts/prepare-als-calibration.py capture /private/new-als-capture
# Transfer that private directory to this same physical J716s, then:
python3 scripts/prepare-als-calibration.py check /private/new-als-capture
python3 scripts/prepare-als-calibration.py prepare /private/new-als-capture \
  --output outputs/firmware/j716s-als
```

The serial-hash gate prevents another unit's calibration being staged. Factory
plists/calibration remain private. This does not install or qualify the sensor.


## 4. Prepare your matching firmware

Acquire the complete profile-pinned IPSW locally. For example, with an authorized Apple download and sufficient free space:

```sh
export IPSW="$HOME/Downloads/UniversalMac_27.3_26C5035r_Restore.ipsw"
curl --fail --location --continue-at - --output "$IPSW" \
  'https://updates.cdn-apple.com/2026FallSeed/53df7654-4865-4259-8dae-30177b76f104/UniversalMac_27.3_26C5035r_Restore.ipsw'
python3 -B scripts/prepare-firmware.py \
  --ipsw "$IPSW" --installer work/sources/asahi-installer \
  --output outputs/firmware/j716s-26C5035r-linux
```

The script checks the complete 27,206,093,542-byte IPSW and its SHA-256, exact Mac17,8/J716s build identity, converter source blobs, global N1 manifest, FTAB component digests, and qualified trackpad output. It refuses an existing output directory. macOS source conversion uses system libcompression; Linux source conversion additionally requires `lzfse-libs`. The exact packaged alternative on an authorized Fedora 43 ARM64 build host is `python3-asahi_firmware-0.9.1-1.fc43.aarch64`; this is a build dependency, not an instruction to install software on the target.

Outputs under `outputs/firmware/j716s-26C5035r-linux/` include `apple/centauri/j716s-ftab.bin`, `apple/tpmtfw-j716s.bin`, and their provenance manifests. Keep these private. The N1 checks do not independently establish Apple's certificate chain.

## 5. Build the complete Omarchy image

The image build is offline construction in a separate guest; it does not install onto the Mac. Build it before rescue if you want to qualify native RAM graphics before any persistent write.

```sh
bash outputs/persistent-install/build-image.sh --check
bash outputs/persistent-install/build-image.sh --build --runtime apple \
  --guest j716s-image-build-owner-01 \
  --output "$PWD/outputs/rootfs/owner-build-01/omarchy-j716s.img.gz" \
  --firmware-dir "$PWD/outputs/firmware/j716s-26C5035r-linux" --size-gib 32
python3 -B outputs/persistent-install/deploy-rootfs.py verify-image \
  --manifest outputs/rootfs/owner-build-01/root-manifest.json \
  --image outputs/rootfs/owner-build-01/omarchy-j716s.img.gz
```

Use a new output directory. The guest verifies the official ARM tarball's detached signature, installs signed packages, builds private Mesa, retains ACLs/xattrs/ownership, validates ext4, and exports image/package/identity manifests. The successful guest is stopped but retained with its root tree at `/var/tmp/j716s-root-build/root`. No actual owner's home or credentials are used.

`--runtime docker` and `--runtime podman` are optional **native Linux ARM64** image routes; this image builder rejects x86/emulation and physical Apple Linux hosts. It uses guest `CAP_SYS_ADMIN` and private PID/mount namespaces. Kernel/Aura build launchers have a broader emulation option; do not confuse those routes. If signed package contents exceed 32 GiB, increase image size and use a fresh output directory rather than skip required packages.

## 6. Fresh target preparation — destructive, physical macOS owner

The target must still have the fresh three-partition Apple baseline: ISC, retained macOS APFS container, and system Recovery. Another System/Data group in the retained APFS container is not another GPT partition. Existing Linux installations are not accepted by this fresh-install path.

```sh
python3 -B outputs/deployment/preflight.py --check --linux-gb 150 \
  --output outputs/deployment/preflight-report.json
bash outputs/deployment/prep-stub-j716s.sh --check --linux-gb 150
# Only after reviewing successful checks, current backup, and owner authorization:
sudo bash outputs/deployment/prep-stub-j716s.sh --apply --linux-gb 150
```

Apply prompts for `BACKUP VERIFIED` and `CREATE`, verifies the actual source-volume owner, rediscovers the complete baseline, and creates the 6 GB paired stub, 500 MB FAT32 ESP, and 150 GB **unformatted** Linux partition. It copies paired boot/restore assets and prints a unique journal directory; it does not enroll or boot anything. Preserve a partial journal; do not restart fresh preparation against it. See [recovery](recovery.md).

Read the **exact printed completed receipt**, not the newest directory or a sample UUID:

```sh
read -r -p 'Exact printed path to completed target.json: ' TARGET
ESP=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["esp_partition_uuid"])' "$TARGET")
SYSTEM_UUID=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["system_volume_uuid"])' "$TARGET")
STUB_PARTUUID=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["stub_partition_uuid"])' "$TARGET")
```

Keep these actual identities and the receipt privately available for paired Recovery. If building elsewhere, transfer generated artifacts privately into the same repository layout on the target Mac; never commit them.

## 7. Build ESP-bound RAM rescue

The primary reproduction route includes a native RAM desktop closure from the completed image guest. Restart **only that successful build guest**, not a failed image build. On macOS:

```sh
container start j716s-image-build-owner-01
container exec j716s-image-build-owner-01 dnf -y install dtc
container exec j716s-image-build-owner-01 python3 /workspace/scripts/build-rescue.py \
  --esp-partuuid "$ESP" --fetch-alpine \
  --firmware /workspace/outputs/firmware/j716s-26C5035r-linux \
  --desktop-root /var/tmp/j716s-root-build/root
container stop j716s-image-build-owner-01
```

On a native Linux ARM64 image host, replace `container` with the selected image engine. The builder writes only local artifacts: pinned Alpine RAM userland, rescue/root initramfs, KIS-reserved DTB, and an ESP-bound rescue Stage 2 with three external hash-bound files. It refuses existing canonical outputs. The private graphics closure copies explicit binaries/data/dependencies, not a real user's state.

A console-only rescue can instead be built with host Python and `fdtput` using `python3 -B scripts/build-rescue.py --esp-partuuid "$ESP" --fetch-alpine`. Choose this **instead of**, not after, the graphical build. Console enumeration does not qualify native rendering; complete the graphics qualification before relying on persistent desktop bring-up.

The historical `qualified` filename identifies the recipe, not a newly passed hardware test. All fresh outputs require physical qualification.

## 8. Configure raw Stage 1 and arm the stub

The receipt-bound stub volumes must be mounted. Mount the recorded ESP writable **before** preparation/arming; the tools revalidate actual GUID/type/disk/mount identity:

```sh
sudo diskutil mount "$ESP"
```

If DiskArbitration refuses the 4K-sector FAT, inspect `diskutil info -plist "$ESP"` and verify its GPT GUID, EFI type, and recorded physical disk. Resolve its actual device for the existing FSKit mechanism, using a new empty mount directory:

```sh
ESP_DEVICE=$(diskutil info -plist "$ESP" | plutil -extract DeviceIdentifier raw -o - -)
ESP_MOUNT=$(sudo mktemp -d /Volumes/omarchy-esp.XXXXXX)
sudo mount -F -t msdos "/dev/$ESP_DEVICE" "$ESP_MOUNT"
```

Never substitute an example disk number or format the ESP again to solve a mount refusal. Use a **new** local Stage 1 workspace and the stable `aurora/boot.bin` selection path. A different Stage 1 path cannot later select the persistent root through this deployer.

```sh
PREPARED=outputs/deployment/stage1-owner-01
bash outputs/deployment/prepare-stage1-j716s.sh --check --stub-receipt "$TARGET" \
  --esp-partuuid "$ESP" --stage2-path aurora/boot.bin --output-dir "$PREPARED"
bash outputs/deployment/prepare-stage1-j716s.sh --apply --stub-receipt "$TARGET" \
  --esp-partuuid "$ESP" --stage2-path aurora/boot.bin --output-dir "$PREPARED"
read -r -p 'Exact source/stub recovery owner username: ' OWNER
bash outputs/deployment/finish-install-j716s.sh --check \
  --system-uuid "$SYSTEM_UUID" --stub-partuuid "$STUB_PARTUUID" --stub-receipt "$TARGET" \
  --image "$PREPARED/stage1-boot.bin" --receipt "$PREPARED/receipt.json" --admin-user "$OWNER"
sudo bash outputs/deployment/finish-install-j716s.sh --apply \
  --system-uuid "$SYSTEM_UUID" --stub-partuuid "$STUB_PARTUUID" --stub-receipt "$TARGET" \
  --image "$PREPARED/stage1-boot.bin" --receipt "$PREPARED/receipt.json" --admin-user "$OWNER"
```

Prepare `--apply` writes only its local configured raw image/receipt. Finish `--apply` is the separately authorized target operation: fresh backup prompt, complete identity/dependency recheck, rescue publication, receipt-bound Finish Installation app, and owner-authenticated stub `bless`. It does **not** enroll, change CTRR, or reboot. Partial arming cannot be blindly replayed.

## 9. Enroll only from physical paired 1TR

Cleanly unmount the ESP. The owner shuts down, holds power for Startup Options, and enters the selected stub's **paired one true recoveryOS**, using the recorded startup selection. Ordinary macOS, SSH, generic Recovery, or another group's recovery is not sufficient.

In its physical Recovery Terminal:

```sh
read -r -p 'Actual recorded stub System UUID: ' SYSTEM_UUID
/usr/sbin/diskutil mount readOnly "$SYSTEM_UUID"
SYS=$(/usr/sbin/diskutil info -plist "$SYSTEM_UUID" | /usr/bin/plutil -extract MountPoint raw -o - -)
RES="$SYS/Finish Installation.app/Contents/Resources"
/bin/bash "$RES/mount-targets.sh" --check
# If recorded volumes/ESP are unmounted, explicitly mount only those UUIDs read-only:
/bin/bash "$RES/mount-targets.sh" --apply
GROUP=$(/usr/bin/plutil -extract GROUP raw -o - "$RES/target.plist")
/usr/bin/bputil -d -v "$GROUP"
```

Independently confirm **one true recoveryOS**, **Paired**, and the exact recorded group. If Kernel CTRR is not Disabled, the owner-authorized manual change is `bputil -nc -v "$GROUP"` for that **stub only**. The scripts do not perform this policy change. Then:

```sh
/bin/bash "$RES/step2.sh" --check
/bin/bash "$RES/step2.sh" --apply
```

Apply rechecks firmware/AC/paired recovery/CTRR, all source/stub/ESP identities and hashes, prompts for fresh backup and approval, and invokes receipt-bound `kmutil configure-boot --raw --entry-point 2048 --lowest-virtual-address 0` with volume-owner authentication. Only success restores the stub's normal bootable state. It never automatically retries or reboots. Do not alter retained macOS's security policy.

## 10. Physically qualify fresh RAM rescue

Unmount the ESP cleanly, then manually boot the recorded stub with independent KIS capture already running. Verify kernel entry, all CPUs, storage GUID enumeration, physical keyboard/trackpad, and safe return to retained macOS. Rescue does not automatically mount target block devices.

With the graphical rescue, run `start-desktop` from its RAM shell. Native GPU preflight must pass before Hyprland starts; verify actual native frames, full-notch panel, and input. Exiting the compositor returns to RAM rescue. A banner, device enumeration, or nonempty log alone is not qualification.

Save the result privately with the actual artifact hashes. Do not proceed with a failed or unqualified rescue. Return to the retained firmware-matched macOS, preserve the same target/prepared receipts, remount its recorded stub volumes and ESP, and leave Linux unformatted/unmounted.

## 11. Deploy persistent root, then complete owner setup

Re-establish `TARGET`, `PREPARED`, and `OWNER` from the same actual records; do not rediscover by volume name or directory age. Read-only raw access may require `sudo` even for a check.

```sh
sudo python3 -B outputs/persistent-install/deploy-rootfs.py deploy --check \
  --stub-receipt "$TARGET" --prepared "$PREPARED" --admin-user "$OWNER" \
  --manifest outputs/rootfs/owner-build-01/root-manifest.json \
  --image outputs/rootfs/owner-build-01/omarchy-j716s.img.gz \
  --initramfs outputs/boot-candidate/j716s-root-initramfs.cpio.gz
```

After the successful check, current backup, physical rescue qualification, and explicit owner authorization, repeat the same arguments with `deploy --apply` **instead of** `--check`. Apply writes/readbacks only the fresh unformatted Linux partition and hash-bound ESP files, retains `aurora/rescue.bin`, and publishes stable `aurora/boot.bin` last. It does not partition, reenroll, or reboot. Existing formatted targets/partial deployments are refused rather than overwritten.

Allow at least 60 seconds of disk idle after the large write, cleanly unmount the ESP, and cold-boot the recorded stub. Physical tty1 verifies actual root/ESP/build identity, grows ext4 with explicit capacity, and asks for your own account/password. No default credentials, autologin, forced SSH, or passwordless sudo are installed. Complete Omarchy's stock first-run in the authenticated SDDM/Hyprland session.

From the owner terminal, check the actual installed surface:

```sh
systemctl is-active j716s-boot-bind.service
systemctl status --no-pager j716s-boot-bind.service j716s-firstboot.service sddm.service
sudo /usr/local/lib/j716s/policies/j716s-policy.py boot
/usr/local/bin/j716s-gpu-preflight --allow-dcp
hyprctl monitors -j
hyprctl configerrors
findmnt -nro SOURCE,FSTYPE / /boot
```

Require native chip `0x6050`/generation 17 and DCP scanout, full-notch 3456×2234 at 120 Hz, no compositor configuration errors, authenticated input/desktop, and verified filesystem/ESP identities. Use preferred monitor discovery; do not freeze a DRM card or transplant probe settings into the owner's configuration. Do not run the binding service script manually as a read-only test: it owns verified remount/growth.

Qualify networking and a controlled restart/return to macOS separately. Audio, suspend, external displays, camera, Touch ID, and daily-use stability remain outside the observed qualification. Keep speaker safety disabled.

## Discovery-only SEP candidate

This is preparation for an attended J716s SEP experiment, not Touch ID setup.
It requires the pinned source archive and the previously verified baseline
`.config` in `outputs/kernel-open-wifi-candidate/`. The builder snapshots the
installed `/boot/aurora/root/t6050-j716s.dtb` to retain its exact KIS states.
Run on the installed native ARM64 J716s, with Docker and passwordless sudo:

```sh
JOBS=6 bash scripts/build-touchid-probe.sh --build
sudo python3 -B scripts/stage-sep-probe.py --stage --acknowledge-risk
```

The builder creates `outputs/kernel-sep-probe-candidate/` and release
`7.3.0-j716s-8ab10100a4c1-sep-probe` in an isolated Fedora guest. It refuses
existing outputs, verifies pinned inputs, preserves the baseline configuration
and all six KIS node states, and records build/patch/artifact provenance.
The distinct J716s profile uses the measured `0x1c000` registration capacity;
the sibling J714s profile is unchanged.

Staging validates the running baseline, authorized open-WiFi or bringup installation,
installs separate modules and `aurora/sep-probe` ESP dependencies, records
`/etc/j716s/sep-probe.json`, and updates the receipt validator with rollback
protection. The default payload, rescue, existing boot dependencies, guard and
spent one-shot marker remain unchanged by staging. Arming and cancellation are
separate explicit operations; no command reboots, and persistent SEP selection
is forbidden.

The candidate carries `apple_sep.xart_writes=0`, `provision_keybag=0`,
`xart_guard=2`, `trusted_keys=0`, `filevault_api=0`, and `keystore_only=1`.
Its module rejects unsafe parameters before DT/DMA/mailbox changes and refuses
`complete_attach`. SPI2 and the sensor remain disabled. No fingerprint service,
PAM change, factory-data installation, enrollment, or verification is included.

Schedule the separate attended cold-boot experiment only after reviewing
[the measured limits](hardware-status.md#touch-id-investigation--2026-11-14)
and [recovery](recovery.md). A one-shot registration can leave SEP unresponsive
until a physical power cycle. Do not open `/dev/sep-bio`, reset enclave state,
enable writes, or treat a successful build/stage as hardware qualification.

### Select the attended discovery trial once

Prefer a current verified backup and retained bootable macOS. Dual boot is a
recovery route, not an independent backup: both OSes share the SSD and SEP.
After saving work and confirming physical Startup Options/power-cycle access:

```sh
sudo python3 -B scripts/stage-sep-probe.py --arm --acknowledge-risk \
  --attended-cold-boot --backup-verified
```

An owner who explicitly accepts proceeding without that backup must instead use
`--waive-backup`; the private arming receipt records `waived-by-owner`, not a
verified-backup claim. This exception does not authorize sensor access,
enrollment, FileVault/trusted-key API use, manual keybag calls or enclave-storage
writes. The discovery bootstrap may open the existing key store in non-writing
keystore-only mode; this does not authorize provisioning or persistence.

Arming verifies the candidate and exact DT delta, preserves every default/root/
rescue dependency, and writes only the existing `aura-try` marker in place plus
`/etc/j716s/sep-probe-arming.json`. Direct-disk readback or validation failure
restores the old marker. The current kernel and persistent default are unchanged.

Before shutdown, cancel if needed:

```sh
sudo python3 -B scripts/stage-sep-probe.py --cancel
```

Cancel can disarm this exact trial even if candidate modules are damaged; it
never replaces the default. Do not overwrite another candidate's armed marker.
An existing arming receipt requires inspection before another experiment.

Shut down fully, then start Linux. Expect
`7.3.0-j716s-8ab10100a4c1-sep-probe` and successful boot binding. The marker is
spent before trial loading, so the following ordinary Linux start should return
to bringup; fallback after a failed trial is not physically qualified.

Only after verifying that trial boot, inspect the module plan:

```sh
modprobe -n -v apple_sep
```

Require exactly one copy of each safe option: `xart_writes=0`,
`provision_keybag=0`, `xart_guard=2`, `trusted_keys=0`, `filevault_api=0`,
`keystore_only=1`. kmod imports these from the trial kernel command line.
Do not append them again: Rust parameters are write-once, and duplicates return
`EEXIST` before driver initialization; kmod can report success despite no module.
If the plan is missing, duplicated or different, stop before loading.

```sh
sudo modprobe apple_sep
```

Record the attempt before loading and inspect the bound driver and readiness
journal, not only modprobe's exit code. Do not unload/reload to retry registration,
open `/dev/sep-bio`, write `complete_attach`, enroll, or reset enclave state.
Capture the first registration result and arrange a cold power cycle/macOS
check afterward. See [recovery](recovery.md#attended-sep-trial-recovery).

## Sensor-authentication SEP candidate

Release `7.3.0-j716s-8ab10100a4c1-sep-sensor` adds one bounded, opt-in
(`apple_sep.sensor_auth_probe=1`) sensor identity/nonce/authentication attempt
on top of the discovery parameters. It never uploads the sensor patch or
calibration, enrolls, matches, writes enclave storage or retries. A refusal or
any non-idle sensor state stops the attempt; a stop is not authentication success.
The pinned source authenticates before patch upload; J716s sensor states are
not yet observed on hardware.

```sh
JOBS=6 bash scripts/build-touchid-probe.sh --build-sensor
```

The build runs the actual Rust admission fixture in the guest before compiling.
Its DT adds `fingerprint@0` on SPI2, enables the SPI2 and nub-GPIO power domains,
switches SPI2 to the reference clock, frees AP GPIO 138 for the sensor interrupt,
and makes nub GPIO a plain GPIO provider (pin 21 only; no interrupt controller).
Staging admits exactly that delta plus path-matched phandle renumbering, and
refuses if any baseline node uses nub GPIO as an interrupt parent.

### Same-unit factory material

The normal bringup kernel exposes only a 16-byte address/length descriptor for
the local policy manifest, pointing outside every exposed or reserved region.
Preparation refuses rather than reading that memory. Boot the staged discovery
candidate once (no `modprobe apple_sep` is needed); its SEP alias makes Aura
forward `local-policy-manifest` bytes. On that boot:

```sh
sudo python3 -B scripts/prepare-sensor-firmware.py prepare \
  --device /dev/nvme0n1pN --partition-uuid <iBootSystemContainer GPT UUID> \
  --output <fresh private directory>
```

Retain the printed provenance SHA256 separately. The firmware binding records
only that the current mBoot/OS labels match the pinned 26C5035r profile;
`current_restore_build_verified` stays `false`.

Rearming discovery requires the installed validator to match this repository
and the previous arming record to be reviewed and moved aside:

```sh
sudo python3 -B scripts/stage-sep-probe.py --update-validator
sudo mv /etc/j716s/sep-probe-arming.json /etc/j716s/sep-probe-arming-<date>.reviewed.json
```

### Stage, then arm separately

Still on the discovery boot (the package check needs the forwarded policy):

```sh
sudo python3 -B scripts/stage-sep-probe.py --variant sep-sensor --stage --acknowledge-risk \
  --sensor-package <package directory> --sensor-provenance-sha256 <retained digest>
```

Staging installs the anchored package under `/var/lib/j716s/sep-sensor-material/`
(root-only) and its two containers under `/usr/lib/firmware/apple/`, binds them
and the unit serial digest into `/etc/j716s/sep-sensor.json`, and rolls back
every created file on failure. Arming, cancellation and the attended cold boot
follow the discovery procedure with `--variant sep-sensor`. Persistent selection
remains forbidden.

The ESP is 476 MB; staging refuses without about 32 MB free and never deletes files.
Move unreferenced archives off the ESP first.

## Touch ID kernel and Omarchy integration

Touch ID follows the M2/M3 path: Omarchy's fingerprint setup asks the owner,
omarchy-mac turns on enclave writes and keybag provisioning, fprintd opens
`/dev/sep-bio`, and the driver uploads the sensor patch and calibration,
enrolls and matches. The `touchid` kernel is the bringup kernel (same config)
plus the SEP probe, sensor wiring and `kernel-j716s-touchid.patch`. The patch
gives J716s the J714s Touch ID profile with the measured `0x1c000`
registration capacity, used whenever `keystore_only` is not 1. The driver
refuses to load unless `trusted_keys=0`, `filevault_api=0` and the xART guard is on.

```sh
JOBS=6 bash scripts/build-touchid-probe.sh --build-touchid
sudo python3 -B scripts/deploy-bringup.py --variant touchid \
  --baseline-config outputs/kernel-open-wifi-candidate/.config
sudo python3 -B scripts/deploy-bringup.py --variant touchid --activate --acknowledge-unbooted-risk \
  --baseline-config outputs/kernel-open-wifi-candidate/.config
sudo scripts/j716s-touchid-overlay.sh install
```

Deployment requires the exact sensor-trial DT delta and preserves the previous
default for rollback. The overlay admits J716s in omarchy-mac's Touch ID
eligibility (which otherwise reports `unsupported:board`), reads the calibration
as the first `firmware-name`, and requires the prepared `mesacal-j716s.bin` and
`msrk-j716s.bin`. It also writes `/etc/modprobe.d/j716s-touchid.conf`
(`trusted_keys=0 filevault_api=0 xart_guard=1`), and a pacman hook re-applies it
after omarchy-mac updates; if it no longer applies, Touch ID reports unsupported.
`j716s-touchid-overlay.sh remove` restores omarchy-mac's files.

With that options file present, the one-shot SEP discovery/sensor trials cannot
load the driver (their command-line parameters would be duplicated); remove the
overlay before repeating them.

After rebooting into the `touchid` kernel, run `omarchy-setup-security-fingerprint`.
It explains the shared-enclave risk and asks before turning Touch ID on.

## Source maintenance and checks

```sh
python3 -B outputs/deployment/update-deployment-provenance.py --check
make check
# After source preparation, with clang available:
bash reference-source/qualified-aura/tests/run-t6050-display-clocks.sh
```

For intentional source changes, regenerate deployment self-hash provenance with its workspace-only `--apply`, then regenerate the release source manifest for your new release. Never use provenance regeneration to bless an unexplained target failure or mismatched runtime artifact. Package repositories/toolchains are external inputs; see [reproducibility](provenance.md).
