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

### Exact-build ProRes preparation and engine diagnostics

`scripts/prepare-prores-firmware.py` extracts T6050-selected native init tables
from the **inspected 26C5035r krnl IM4P** in this Mac's retained Preboot. It pins
both compressed and decoded source hashes and final APRI bytes; no arbitrary
row map, foreign chip or guessed register values are accepted.

```sh
python3 -m venv work/build/prores-venv
work/build/prores-venv/bin/pip install lzfse==0.4.2
# KERNELCACHE is the private, exact-build krnl IM4P collected read-only.
work/build/prores-venv/bin/python -B scripts/prepare-prores-firmware.py \
  --kernelcache "$KERNELCACHE" --output outputs/firmware/new-j716s-prores
work/build/prores-venv/bin/python -B scripts/prepare-prores-firmware.py \
  --check outputs/firmware/new-j716s-prores
```

Run as the owner of the private inputs; directories/files must remain private.
The output contains a 2,880-byte APRI candidate, not a hardware pass. No install
or engine-start operation exists in this tool. ProRes needs a separately
reviewed installation and attended kernel/engine qualification.

AVE's current 26C5035r generation does not match the driver's 26A428 ABI. ANE
has a loader preload, but its TEXT/DATA geometry differs from the pinned
profile. `patches/kernel-j716s-media-handoff-diagnostics.patch` can be applied
to the exact clean kernel source for an isolated diagnostic build; it improves
rejection messages and safe TEXT observation, **not admission**. It is not
part of any selected/deployed candidate. Do not remove size/pin/cold-handoff
checks to make these engines start. See [hardware status](hardware-status.md#exact-build-encoder-and-neural-engine-progress--2026-11-14).


### Same-unit speaker tuning preparation (no enablement)

`scripts/prepare-speaker-tuning.py` collects real AID41 tuning and the six-speaker
factory calibration from this Mac's retained **26C5035r System** volume. It
requires a read-only mount, J716s/T6050 identity, the running unit's serial hash
and the same valid speaker-vendor strap. It never starts a daemon, changes UCM,
unmasks audio, installs firmware or changes boot selection.

Fetch the pinned MIT-licensed collector archive into ignored local build state:

```sh
mkdir -p work/build/speaker-bringup
curl --fail --location --output work/build/speaker-bringup/installer.tar.gz \
  https://codeload.github.com/aurora-silicon/aurora-installer/tar.gz/e8f0711f8f77cc5fdaf3f78348321d865a3818bc
# The preparation tool checks SHA-256 before extracting/executing source.
sudo python3 -B scripts/prepare-speaker-tuning.py \
  --source-archive work/build/speaker-bringup/installer.tar.gz capture \
  --macos-root /run/j716s-macos-system/root \
  --output /var/lib/j716s/speaker-tuning/prepared-26C5035r
sudo python3 -B scripts/prepare-speaker-tuning.py \
  --source-archive work/build/speaker-bringup/installer.tar.gz verify \
  --package /var/lib/j716s/speaker-tuning/prepared-26C5035r
```

The directory must be new and remains private (0700/0600), including incomplete
attempts. Do not publish the package or reuse another unit's calibration.

For read-only System access on Apple Silicon, the upstream `apfs-fuse`
`66b86bd525e8cb90f9012543be89b1f092b75cf3` reader needs
`patches/apfs-fuse-unencrypted-system.patch`: failure to unwrap the hardware
container keybag must not reject an **unencrypted** System/Preboot volume.
Encrypted volumes still require their own keys. The reader opens the disk
O_RDONLY; no write-capable APFS driver or FileVault credentials are needed.

Build in ignored local state with native CMake, C++, FUSE3, zlib and bzip2:

```sh
cd work/build/speaker-bringup
curl --fail --location -o apfs-fuse.tar.gz \
  https://codeload.github.com/sgan81/apfs-fuse/tar.gz/66b86bd525e8cb90f9012543be89b1f092b75cf3
echo 'e78e71e87d8ba182822ec34233189141b8816c9a3f700c9a22f6dc1480afee51  apfs-fuse.tar.gz' | sha256sum -c -
tar -xzf apfs-fuse.tar.gz
curl --fail --location -o lzfse.tar.gz \
  https://codeload.github.com/lzfse/lzfse/tar.gz/e634ca58b4821d9f3d560cdc6df5dec02ffc93fd
echo 'ca98aa6644d44500e3315858daa747ce15bd06d49e3edb12a5458e5525e8ebdb  lzfse.tar.gz' | sha256sum -c -
tar -xzf lzfse.tar.gz --strip-components=1 \
  -C apfs-fuse-66b86bd525e8cb90f9012543be89b1f092b75cf3/3rdparty/lzfse
patch --batch --fuzz=0 -p1 -d apfs-fuse-66b86bd525e8cb90f9012543be89b1f092b75cf3 \
  < ../../../patches/apfs-fuse-unencrypted-system.patch
cmake -S apfs-fuse-66b86bd525e8cb90f9012543be89b1f092b75cf3 -B apfs-build \
  -DCMAKE_POLICY_VERSION_MINIMUM=3.5
cmake --build apfs-build --target apfs-fuse apfsutil -j6
# Substitute the rediscovered retained-macOS partition, not a whole disk.
sudo apfs-build/apfsutil /dev/RETAINED_MACOS_PARTITION
# Read the reported role/index: mount System only, never encrypted Data.
sudo mkdir -p /run/j716s-macos-system
sudo apfs-build/apfs-fuse -o ro,allow_other -v SYSTEM_INDEX \
  /dev/RETAINED_MACOS_PARTITION /run/j716s-macos-system
```

Unmount with `sudo umount /run/j716s-macos-system` after collection. If a snapshot
is needed, use the System snapshot XID reported by `apfsutil` with `-o snap=XID`;
the capture tool still requires the exact System build. Never use lax mode.

The factory bundle removes a missing-data blocker, **not** the safety admission
gate. The old J716 profile copies unmeasured J316 thermal parameters; do not
install it to force playback. The newer `speakerguardd` v2 protection stack is
explicitly alpha/not-for-use. Its reader/compiler may be exercised offline;
deploying it and qualifying sense mapping, thermal parity, DSP routing and the
dead-daemon interlock are separate work. Keep the existing speaker guard intact.


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

Once genuine CalibrationData has been prepared, compute the private manifest's
SHA-256 and supply it explicitly to the installer. The dry-run checks the live
boot guard, exact firmware pairing, all source hashes and same-unit identity:

```sh
sha256sum outputs/firmware/j716s-als/als-calibration-manifest.json
sudo python3 -B scripts/prepare-als-calibration.py check-install \
  outputs/firmware/j716s-als --manifest-sha256 MANIFEST_SHA256
# Only after the dry-run accepts this genuine package:
sudo python3 -B scripts/prepare-als-calibration.py install \
  outputs/firmware/j716s-als --manifest-sha256 MANIFEST_SHA256
sudo python3 -B scripts/prepare-als-calibration.py rollback
```

Installation publishes firmware and a receipt, without restarting or loading
AOP. Rollback refuses changed/replaced bytes or publication inodes. Do not
substitute the recovered HmCA factory IMG4: its equivalence to CalibrationData
is unproven. `recover-factory --device /dev/ISC_PARTITION --partition-uuid
ACTUAL_GPT_PARTUUID --output /private/new-hmca` preserves that evidence only.

Camera preparation uses the read-only System mount described above and the
current boot's raw ADT, not the stale physical ranges of an older overlay:

```sh
sudo python3 -B scripts/prepare-camera-firmware.py prepare \
  --system-root /run/j716s-macos-system/root \
  --output outputs/firmware/j716s-26C5035r-camera-private
# Save the manifest SHA-256 printed by prepare independently, then:
sudo python3 -B scripts/prepare-camera-firmware.py check \
  outputs/firmware/j716s-26C5035r-camera-private --manifest-sha256 MANIFEST_SHA256
```

It extracts only the exact-build sensor-tuning candidate and records the
current handoff. No ISP startup, privacy-indicator operation, firmware install
or sensor-identity proof is implied. The package is boot-bound; collect again
after a different handoff, rather than reusing old TEXT/DATA physical addresses.

For passive Bluetooth voice-protocol evidence, without repeating a recording:

```sh
sudo python3 -B scripts/capture-bluetooth-sco.py --controller hci0 \
  --duration 30 --output /var/lib/j716s/new-sco-evidence
```

The locked kernel filter discards unrelated/key traffic and SCO payloads before
userspace. Only negotiation metadata and aggregate SCO header counts are kept.
The tool does not select HFP, connect/disconnect peers or send HCI commands.
Monitor TX counts requests, **not successful firmware delivery**; unopened RX
rings are invisible. Exact Centauri SCO framing/routing/credits still need
evidence before a transport patch can be justified.



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

The pinned kernel exposes `/dev/sep-bio` interface version 5. Omarchy's
observed `libfprint-git 1:1.94.100.r10.g6f9479c-5` accepts version 4 only and
fprintd ignores the reader. Use the pinned
[`packaging/libfprint-git-aurora/PKGBUILD`](../packaging/libfprint-git-aurora/PKGBUILD)
for v4/v5 support before enrollment. It packages libfprint 1.94.100 plus the
complete Aurora userspace patch series and the pinned kernel UAPI header.
Build in ignored local state; sign the package with a locally trusted key
when the target requires local-file signatures:

```sh
mkdir -p work/libfprint-aurora/rebuild
cp packaging/libfprint-git-aurora/PKGBUILD work/libfprint-aurora/rebuild/
(cd work/libfprint-aurora/rebuild && makepkg --syncdeps --sign)
```

Install the resulting trusted package with `pacman -U`, then restart fprintd
if it was running against the old library. No kernel reboot is needed solely
for this userspace replacement. Epoch 2 prevents the observed v4-only package
from replacing it automatically; explicitly reinstall the repository package
once that package supports v5. Do not globally disable signature verification.

After rebooting into the `touchid` kernel, run `omarchy-setup-security-fingerprint`.
It explains the shared-enclave risk and asks before turning Touch ID on.

Successful setup enrolls and matches before configuring sudo, polkit and the
Quickshell lock screen. SDDM remains password-based. If a finger is already
enrolled, verify it with `fprintd-verify` instead of rerunning enrollment.
Enrollment and configured PAM files do not by themselves prove each actual
authentication surface or matching after a cold boot.

## Speaker-priority and SCO qualification

Normal speakers remain guarded. The `touchid` kernel already has the root-only
protection interface; the T6050 transport patch and silent hardware/interlock
checks do not admit unvalidated DSP or controller behavior. Use the same-unit
factory bundle, never another chassis's thermal profile.

`scripts/capture-speaker-reference.py prepare` accepts the pinned protection
archive, collector archive, private factory package and hash-bound compiler.
It produces a new private portable reference request. On the exact paired
macOS, `capture` verifies the independent request-manifest hash, unit/build and
native AudioDSP hash before compiling the offline AudioUnit host. It opens no
audio device. `compare` verifies the returned manifest and uses a matching
compiler/voicing-render revision. Preparation/comparison needs NumPy; native
capture needs Python 3.11+ and Xcode command-line tools with the owner-reviewed
license accepted. Apple's bundled Python 3.9 lacks `hashlib.file_digest`; select
an already installed newer Python in `PATH` before running the capture launcher.
See each subcommand's `--help` for its required paths and external hash anchors.

Portable requests store `AID41/` directly under `tuning/`; capture and comparison
must use that directory, not a nonexistent `tuning/audio-tuning/`. The native
camera daemon can have multiple hardlinks in the read-only System snapshot.
Only its explicit System-source read admits root-owned read-only hardlinks;
private packages, symlinks and untrusted/writable sources remain rejected.


The first capture covers full-volume voicing only. Controller-state parity,
volume-dependent laws, generated graph timing and protected playback remain
qualification gates. Do not install an attestation by editing its verdict,
strip new tuning metadata, or unmask playback from a silent-test pass alone.
Each new kernel/blob needs its own silent wiring check. Keep raw tuning,
reference audio and transfer keys private; if using an encrypted transfer,
verify its independently retained ciphertext hash before decrypting/extracting.

The isolated standard-SCO candidate preserves Touch ID's config and sensor
DT contract and adds the native transport and ProRes ownership quarantine:

```sh
# Fresh workspace only; existing candidate outputs are refused.
JOBS=6 bash scripts/build-touchid-probe.sh --build-touchid-sco
sudo python3 -B scripts/deploy-bringup.py --variant touchid-sco \
  --baseline-config outputs/kernel-open-wifi-candidate/.config
# Explicit authorization of an unbooted default, only after the check passes:
sudo python3 -B scripts/deploy-bringup.py --variant touchid-sco --activate \
  --acknowledge-unbooted-risk --baseline-config outputs/kernel-open-wifi-candidate/.config
# Restore the retained, authorized previous Touch ID/default payload:
sudo python3 -B scripts/deploy-bringup.py --variant touchid-sco --rollback
```

No command above reboots or replaces the running kernel's modules. After an
attended boot, verify the new release, actual boot guard, display/input/network,
Touch ID and speaker guarding before a bounded headset microphone check.
Private four-byte HDR-SCO is unsupported; normal HCI credits stay with the core.
Transport faults stop the driver and require fresh firmware, not repeated HCI
down/up. ProRes quarantine does not authorize DMA: failed completion can retain
STREAMOFF/close until physical machine recovery. Do not install/start APRI yet.

For the same paired-macOS visit, camera `capture-macos` and ALS
`capture-evidence` require the Linux unit-serial SHA256 supplied independently.
Camera stays off; empty/missing registry evidence is recorded, not converted to
support. Only one genuine **193-byte** illuminance CalibrationData may enter
the ALS installer. HmCA/color data is not interchangeable. Installation alone
does not activate or qualify ALS; kernel acquisition and lux behavior still
need their own checks.

## Guarded speaker runtime

`scripts/prepare-speaker-runtime.py prepare` verifies the original pinned
source, same-unit native request/reference and externally anchored parity,
builds a separately bound candidate with native-release/unit-detection/asset
repairs, and requires the complete numerical gate. Its package includes the
actual upstream compiler, supervisor, LV2 graph, session/provision helpers and
preview switch. `install` refuses existing files and enables/starts nothing;
`rollback` removes only unchanged receipt-bound files after the stack is stopped.
See `--help` for independent manifest and receipt hash arguments.

On a dual kernel, initial qualification uses direct `provision run`, then
`selfcheck run --force --switch-interface` on the provisioned full blob, graph
generation and complete `selfcheck verify`. A qualified seat-user graph/session
helper must run before the ordinary upstream preview switch; otherwise the
supervisor correctly cannot grant. Do not write its persistent marker manually.
The switch's installer route must preserve every declared native asset, including
the 80-byte BuzzKill matrix. Never remove metadata to force compilation.

The older `touchid-sco` kernel deliberately clamps J716s at −30 dB even under
a grant, which the unity-gain protection model rejects. Do not weaken that
readback check. The isolated `touchid-sco-spk` candidate changes only the exact
J716s v2 operating ceiling, retaining legacy/global caps and floor/lease gates:

```sh
JOBS=6 bash scripts/build-touchid-probe.sh --build-touchid-sco-spk
sudo python3 -B scripts/deploy-bringup.py --variant touchid-sco-spk \
  --baseline-config outputs/kernel-open-wifi-candidate/.config
```

An owner-issued restart and matching-kernel zero-only safety checks are required
before preview audio. Baseline/rescue and the previous SCO/default payload are
preserved by deployment; the current running kernel's modules are never replaced.
Preview is experimental and requires explicit acknowledgement, not a claim of
independent lifetime or volume-sweep certification.

After booting the selected protected-ceiling kernel, pass the explicit tuning
source to the manual hardware check (omitting it refuses before testing):

```sh
sudo /usr/libexec/speakerguardd/speakerguardd-provision run
sudo /usr/libexec/speakerguardd/speakerguardd-selfcheck run --force \
  --switch-interface --params /var/lib/speakerguardd/params.bin \
  -- /var/lib/aurora-speaker-upgrade/bundle
```

Only after complete graph/numerical verification and fault-withdrawal checks
may the ordinary upstream preview switch grant protection. A playback test
must target `sgv2.voicing.j716` explicitly and check its mute state; successful
sample submission into a muted sink is not acoustic proof. The observed
post-reboot controlled tone was owner-confirmed with protection continuously
granted. Persistent startup uses the system supervisor, seat path and sleep
hook plus the seat-user graph/session units; enabling the sleep hook does not
qualify suspend/resume. Keep preview and repeated-boot limits explicit.

## AirPods legacy synchronous setup candidate

If actual controller evidence rejects Enhanced Setup Synchronous Connection
with status `0x11`, do not count profile availability as microphone support.
The isolated `touchid-sco-spk-hfp` variant adds the standard upstream broken
enhanced-setup quirk only to the T6050 controller; ordinary HCI codec/credit
and failure handling remain. Legacy setup is an experiment, not a proven fix.

```sh
JOBS=6 bash scripts/build-touchid-probe.sh --build-touchid-sco-spk-hfp
sudo python3 -B scripts/deploy-bringup.py --variant touchid-sco-spk-hfp
```

Deployment is permitted only from the current protected speaker kernel,
preserves that default/runtime for rollback and never replaces its modules.
Selection requires explicit owner authorization and the tool's unbooted-risk
acknowledgement. After a manual restart, requalify the speaker graph for the
new kernel before normal playback, then require successful synchronous-link
setup and a real Bluetooth-only microphone recording. Restore the prior
profile/default routes and delete private recording buffers afterward.

## Integrated guarded bringup candidate

`--build-bringup-full` retains the existing protected-speaker/Touch ID stack
and selects exact-build AVE compatibility, bounded ANE build/IPC support,
peripheral wait handling and M5 retained-pool hardening. The existing exact
sensor/KIS DT validator still applies; unproved thermal/HDMI/SD/camera/voice
changes are not included.

```sh
JOBS=6 bash scripts/build-touchid-probe.sh --build-bringup-full
sudo python3 -B scripts/deploy-bringup.py --variant bringup-full
```

The first full candidate requires `ane_t6021.ane_observe_only=1`. This is a
read-only boot/module option and a runtime authorization requirement, not
permission to start ANE: recognized no-map reserved TEXT is measured and
acquisition refuses before observer MMIO, power or DMA. A retained-file hash
cannot replace a live preload pin. Later engine startup requires independently
admitted identity/ownership and an explicitly authorized candidate.

After a convenient manual boot, first verify boot identity and protected
speaker graph. `scripts/test-ave-27_3.py --postboot-recipe` prints bounded
compatibility encode/software-decode steps, not an automatic hardware job.
`scripts/ane-27_3-proof.py` checks real stride-64 fp16 output after the production
H17 compiler and ABI-3 runner; preparing inputs is not job proof. Hardware
attachments, microphones/camera and stress/fan operations are owner-attended,
announced and stopped on the first error; keep private samples out of publication.

## Booted media compatibility checks and ANE metadata

The AVE postboot recipe requires the standard plugins:

```sh
omarchy pkg add gst-plugins-good gst-libav
python3 -B scripts/test-ave-27_3.py --postboot-recipe
```

The recipe verifies the dynamic encoder factory's read-only device binding,
captures actual reference pixels, and checks every decoded frame/channel, not
merely the file size. It uses FFmpeg only for CPU comparison, not its unsupported
V4L2 Main encoder. The current local H.264/HEVC compatibility sessions passed;
do not extrapolate to other profiles or endurance.

`bringup-ane-observe` adds exactly the native J716s `apple,ane-type=0x220` that
the pinned DT/loader omit. Missing/wrong type still fails before TEXT access.
The property is not a cold-ownership marker and cannot authorize acquisition.

```sh
JOBS=6 bash scripts/build-touchid-probe.sh --build-ane-observe
sudo python3 -B scripts/deploy-bringup.py --variant bringup-ane-observe
```

The full sensor/KIS DT check permits only this additional exact cell; all
observation variants require `ane_t6021.ane_observe_only=1`. Preserve the current
tested full candidate for rollback. A later manual boot must first produce the
recognized-reservation TEXT digest; engine/job proof remains independently gated.

## Guarded ANE acquisition and remote cross-build

The corrected observation boot measured the admitted live pin. A separate
`bringup-ane-start` candidate may use `ane_t6021.ane_observe_only=0`; existing
full/observation candidates still require 1. This does not waive cold-state or
ownership checks. Failed stages stop and uncertain acquisition quarantines;
never retry through resets or override the observation module in place.

```sh
python3 -B scripts/build-kernel-remote.py --jump JUMP --host BUILD_HOST \
  --variant bringup-ane-start --jobs 64
```

Remote builds run native x86-64 LLVM/Rust targeting ARM64, not ARM emulation.
Only public inputs upload. Archive/toolchain/source caches remain on the build
host; the target's baseline DTB and private firmware/captures stay local. The
shared finalizer validates raw artifact hashes, ARM64 Image bounds, signed
module architecture/vermagic and source pins before transforming the DTB and
publishing final manifests. Full debug modules are retained for the first
benchmark; do not strip already signed modules. Selection remains an explicit
owner-authorized step and reboot remains manual.

Cross builds also require a real ARM64 userspace sysroot for Kconfig's
`CC_CAN_LINK` probe. The worker validates signed Fedora ARM64 RPM identities
and hashes, stages the sysroot, and binds exact user compiler/linker flags
into the compile receipt. It never forces the capability bit or accepts a
different kernel config. Native builds record a null target sysroot. Cached
source ownership uses its own source signature; old nonempty caches without
that marker are refused rather than relabelled. Build/module caches remain
bound to the full input signature.

## Source maintenance and checks

```sh
python3 -B outputs/deployment/update-deployment-provenance.py --check
make check
# After source preparation, with clang available:
bash reference-source/qualified-aura/tests/run-t6050-display-clocks.sh
```

For intentional source changes, regenerate deployment self-hash provenance with its workspace-only `--apply`, then regenerate the release source manifest for your new release. Never use provenance regeneration to bless an unexplained target failure or mismatched runtime artifact. Package repositories/toolchains are external inputs; see [reproducibility](provenance.md).
