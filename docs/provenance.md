# Sources and reproducibility

## Immutable source selection

[`profiles/j716s.json`](../profiles/j716s.json) records full revisions, public input hashes, the exact Apple firmware pairing, and board/display scope. The source preparation utility verifies its inputs before using them. Build outputs have their own generated hash manifests: a source pin is not a substitute for verifying the supplied binary.

| Input | Revision / origin | Use |
| --- | --- | --- |
| Kernel | [rolotrealanis98/linux `8ab10100a4c1c944797e3fc8345589aff57777df`](https://github.com/rolotrealanis98/linux/tree/8ab10100a4c1c944797e3fc8345589aff57777df) | T6050/J716s, G17S GPU, DCP, storage and input |
| Aura | [aurora-silicon/aura `54678354b70d64506408d9b157f85ac6065d194b`](https://github.com/aurora-silicon/aura/tree/54678354b70d64506408d9b157f85ac6065d194b) | Raw Stage 1, ESP Stage 2; carried exact-firmware and fixed-clock handoff changes |
| Bring-up tools | [rolotrealanis98/m5pro-linux `b4b5bd5de6eebd3265e3068e850ffc3bcf40482d`](https://github.com/rolotrealanis98/m5pro-linux/tree/b4b5bd5de6eebd3265e3068e850ffc3bcf40482d) | Same-board firmware preparation and bring-up references |
| Mesa | [aurora-silicon/mesa `a8e86a80cdc4695d00e7e63b8ebea263e557431b`](https://github.com/aurora-silicon/mesa/tree/a8e86a80cdc4695d00e7e63b8ebea263e557431b) | Private native Vulkan/zink stack |
| Root/stub recipes | [betizzel/omarchy-m5 `7974606ab1de0d41737f2c8784a7e86987612ce1`](https://github.com/betizzel/omarchy-m5/tree/7974606ab1de0d41737f2c8784a7e86987612ce1) | Adapted image and explicit-identity preparation/enrollment |
| Desktop integration reference | [joshuaswarren/m5-omarchy-install `b9d08697efd4060b82abda9cdf58dc3634b34622`](https://github.com/joshuaswarren/m5-omarchy-install/tree/b9d08697efd4060b82abda9cdf58dc3634b34622) | Edge package/provisioning approach; not run unchanged, not a J716s hardware profile |
| ARM base | Official Arch Linux ARM aarch64 tarball and detached signature | Build-system signing fingerprint `68B3537F39A313B3E574D06777193F152BDBE6A6` |
| Package keys | Public Omarchy and Asahi ARM keyrings | Exact source URLs/hashes/fingerprints under `outputs/persistent-install/keys/` |
| Apple firmware | Profile-pinned Apple CDN 26C5035r IPSW | Locally acquired only; exact board and firmware checks |

## What can and cannot be reproduced byte-for-byte

Kernel source, fragments, local version, build identity, and build timestamp are selected explicitly. The original build used ARM64 Fedora 43, LLVM 21.1.8, Rust 1.99.1, and the kernel's own `tools/aurora-bindgen/build.py`.

The kernel generates a new private module-signing key on a clean build. That key stays inside the build guest. Consequently a correct fresh build need not match the original kernel/module artifact hashes. The new generated manifests bind its own kernel, modules, loader, rescue, root image, and deployed dependencies together.

Fedora images, package repositories, Rust downloads, and the official ARM root tarball are external services. Package signatures are verified, but availability and package versions can change. Record and retain the generated build manifests/package inventories locally. This repository does not claim a hermetic package snapshot or byte-identical root image.

The N1 builder checks FTAB bounds, board/production properties, absence of per-unit ticket fields, and manifest SHA-384 component digests. It does not independently prove Apple's certificate chain. Firmware input/output hashes are pinned to the locally verified pairing.

Physical qualification applies to the original deployed artifacts. It is not inherited by a newly compiled binary simply because its source commit matches.

### Open-network Wi-Fi experiment

`patches/kernel-j716s-open-wifi.patch` is an opt-in change to the pinned kernel,
not a newly qualified baseline. Public research found no open-join implementation
in the checked T6050 driver. Aurora's merged
[PR #1725](https://github.com/aurora-silicon/linux/pull/1725) fixes protected joins
and diagnostics; it does not add open-network support.

Read-only inspection of the installed N1 FTAB matched the profile's output
SHA-256 `7f7fe6ce292e925881f8a34d22d590e72dc51faf0d83d6110c2af5ed5bd2e48b`.
Its embedded metadata defines `AciAuthOpen = 0`, `AciAkmNone = 0`, and internal
`CORE80211_CIPHER_SUITE_NONE = 0`. The internal cipher enum is **not established
as the connect-command cipher namespace**: internal AES is 3, while the existing
working protected join writes 4 at command offset 0x48. The experiment preserves
that protected-join value and proposes 0 for a plaintext join. Firmware metadata
does not prove that wire value or the complete open-join TLV shape; native capture
or physical firmware acceptance is still required.

The inherited TLV 304 join mode remains 5, TLV 316 remains the roaming bitmap
0, and the TX/RX data path is unchanged. RX completion-footer bit 7 is not
identified as a decryption flag and must not be bypassed on that assumption.
No Apple firmware bytes or extracted metadata blobs are published. Host tests
establish join-builder behavior only, not working hardware support.


### Open AVD firmware

`scripts/prepare-avd-firmware.py` consumes
[Aurora avd-fw ea3a65cc9c0c41907e6ad2714b813e4be6858485](https://github.com/aurora-silicon/avd-fw/commit/ea3a65cc9c0c41907e6ad2714b813e4be6858485),
archive SHA-256 `e266022f0bedf531431be50efcbe23f29a10efc4b0a64468abcc6c3f7e364b19`.
This MIT-licensed open CM3 firmware is not an Apple executable image. Its queue
ABI 2 and v5/tier2 SRAM aperture match the pinned AVD driver inputs, whose source
hashes are checked before building. The builder records tool versions, ELF hash,
vector checks, upstream host queue tests and two byte-identical clean images.
Observed LLVM 21.1.8 output was 73,728 bytes; independent toolchains may differ.
Generated firmware/provenance stay in ignored local outputs.

Bluetooth and G17 diagnostic patches are source-hash pinned in the bringup
build and runtime validator. The latter is instrumentation only, not a proven
GPU recovery fix. ALS preparation preserves per-unit Apple factory bytes and
checks model/build/serial identity; those bytes and their raw plist evidence
must not be published or substituted between units.

### Same-unit speaker factory tuning

The preparation wrapper executes only
[aurora-installer e8f0711f8f77cc5fdaf3f78348321d865a3818bc](https://github.com/aurora-silicon/aurora-installer/tree/e8f0711f8f77cc5fdaf3f78348321d865a3818bc),
archive SHA-256 `51b6c0f179d98a38ac1e1a1261eb1d94c3956742969eed278ef740fa67bd3c16`.
Its MIT-licensed collector reads AID41's complete tuning directory, macOS 27's
per-AID DSP database, the AudioDSP lattice/BuzzKill matrix and the live ADT's
138-byte same-unit speaker calibration. No Apple tuning is redistributed.
The wrapper rejects missing/foreign unit identity rather than accepting an
open-loop fallback. Its private preparation receipt is not playback certification.

The read-only APFS reader is
[sgan81/apfs-fuse 66b86bd525e8cb90f9012543be89b1f092b75cf3](https://github.com/sgan81/apfs-fuse/tree/66b86bd525e8cb90f9012543be89b1f092b75cf3),
archive SHA-256 `e78e71e87d8ba182822ec34233189141b8816c9a3f700c9a22f6dc1480afee51`,
with `patches/apfs-fuse-unencrypted-system.patch`. Its compressor submodule is
[lzfse e634ca58b4821d9f3d560cdc6df5dec02ffc93fd](https://github.com/lzfse/lzfse/tree/e634ca58b4821d9f3d560cdc6df5dec02ffc93fd),
archive SHA-256 `ca98aa6644d44500e3315858daa747ce15bd06d49e3edb12a5458e5525e8ebdb`.
Upstream licenses remain in fetched source; no reader binaries are published.

The offline protection reader is not installed as a daemon.
[speakerguardd v2](https://github.com/aurora-silicon/speakerguardd/tree/v2)
explicitly remains alpha/not-for-use. The `04169c4470650f8e2554db70d4072fb92e484138`
reader refuses the newer collector's `buzzkill_quadrature` field; that refusal
must not be bypassed by deleting metadata. The schema-aware offline branch
[54c911c7a7098af91f90375ae1b8d09f8588c79a](https://github.com/aurora-silicon/speakerguardd/tree/54c911c7a7098af91f90375ae1b8d09f8588c79a)
uses archive SHA-256 `92e70bdb6210393b386a50f0d3defa4243dd8ec0773002bc09caaaedbeba4964`.
Compile only `aurora-speaker-tuning` with `cargo build --locked --release -p
aurora-speaker-tuning --bin aurora-speaker-tuning` in a fresh output tree.
Reader/parameter validation does not establish macOS parity or safe hardware
operation. Keep all derived blobs and reports private and the speaker guard on.

### ProRes and engine diagnostic candidates

The ProRes recipe pins the retained 26C5035r `krnl` IM4P and decoded
kernelcollection before following native AppleProResHW 627 T6050 table
pointers. It uses `lzfse==0.4.2`, validates all table records and pins the final
APRI bytes; firmware rows are private Apple-derived data, never repository
fixtures. T6050's APRI contract comes from
[upstream native support](https://github.com/aurora-silicon/linux/commit/75eb2744e2bd41ce596972d2c188c6d93d2fb013).

The media diagnostic patch targets kernel
`8ab10100a4c1c944797e3fc8345589aff57777df`, with ANE observation informed by
[79e1e9f88449f2ad8a9a97648f91df8c656157d2](https://github.com/aurora-silicon/linux/commit/79e1e9f88449f2ad8a9a97648f91df8c656157d2).
It changes no accepted layout or firmware pin and is excluded from the deployed
variant recipes. AVE's source qualification is
[26A428-specific](https://github.com/aurora-silicon/linux/commit/55b7401a4b03bbedb487aae09770bafc0a17cefd),
not a qualification of the retained 26C5035r image. A file digest or native
table extraction does not qualify a boot/protocol ABI or hardware operation.



### Touch ID userspace

The local `libfprint-git` recipe pins libfprint 1.94.100 at
[`80a4b5ec612892c5c056c48dddaa561452cf37ec`](https://gitlab.freedesktop.org/libfprint/libfprint/-/tree/80a4b5ec612892c5c056c48dddaa561452cf37ec),
the complete Aurora patch series at
[`a227a350d7ba2753e674b887c5f8304354732ee7`](https://github.com/aurora-silicon/aurora-sep-userspace/tree/a227a350d7ba2753e674b887c5f8304354732ee7/libfprint),
and the selected kernel's `apple-sep.h`. Downloaded patches/header have BLAKE2
checksums in the recipe. The Aurora driver accepts interface v4/v5, unlike
the observed Omarchy v4-only build. Generated packages and signing keys remain
private; epoch 2 deliberately holds this local build until an explicit return
to a v5-capable repository package. Libfprint and the upstream patches retain
their respective licenses; this integration does not relicense them.

### Native speaker, SCO and media contracts

The speaker transport patch targets the schema-compatible protection source
`54c911c7a7098af91f90375ae1b8d09f8588c79a` above. Its description follows
the pinned kernel's
[`HW_CFG_T6050`](https://github.com/rolotrealanis98/linux/blob/8ab10100a4c1c944797e3fc8345589aff57777df/sound/soc/apple/t8152_aop_audio.rs),
not another board's measured audio timing. The native voicing capture reuses
that source's offline AudioUnit host and per-AID differential rig. Original
upstream MIT notices remain in acquired source; no native executable or tuning
is redistributed. Native 26C5035r AudioDSP is not byte-equivalent to recorded
older references; parity cannot be inherited.

`prepare-bluetooth-native.py` pins same-build System/Cryptex executable, profile
and cache-span hashes. The SCO patch records those hashes and native function
addresses as evidence for standard framing, attributes and completion flags.
Those are file/cache-analysis addresses, not runtime MMIO authorization.
The patch targets the selected T6050 driver; standard native rings/framing are
implemented, private HDR negotiation is not. Native reverse engineering does
not qualify headset voice hardware.

Native acquisition now requires the independently retained Linux unit hash;
retained-volume mode also requires externally anchored platform evidence.
`inspect-capture-native.py` binds exact ARM64e dispatch and camera registry
observations without awarding hardware admission. Use matching private-owner
input/output directories; do not weaken single-link or ownership gates.

The ProRes ownership patch is informed by
[`PR925`](https://github.com/aurora-silicon/linux/pull/925#issuecomment-6724518116),
whose parked depth-one experiment reports invalid DART translations. This
candidate avoids shared reset and retains uncertain DMA until machine recovery;
it does not copy unproven reset constants or authorize first DMA. Native media
inspection pins the retained compressed/decoded kernelcollection and function
spans, deriving new AVE command sizes and ProRes/ANE acquisition facts without
shipping Apple code or relaxing layout/digest guards.

Native ALS illumination loading on 26C5035r requires 193 bytes and is separate
from its color path. The
[Asahi extractor](https://github.com/AsahiLinux/asahi-installer/blob/3262ca299aa1ea6334d48b28d6a2671428753ff3/asahi_firmware/als.py)
preserves HmCA separately and supplies no justified conversion. Native camera
platform tables and
[upstream sensor detection](https://github.com/aurora-silicon/linux/commit/8cb07827e780267de659fe0325cd215fb50c3659)
do not establish J716s AP/exclave takeover or privacy. Current
[loader admission](https://github.com/aurora-silicon/aura/blob/2175c4832c5c190aace8abd0c5fa44e475e6b865/src/kboot.c)
still excludes this exact board/build. Paired-macOS acquisition remains private,
source/identity-bound evidence, never a substitute for hardware qualification.

### Guarded runtime and operating ceiling

Runtime preparation verifies the original protection archive and separately
binds the transport, native-release attestation, legacy-safe unit identification
and declared-matrix preservation repairs and their regression inputs. It builds
from a private copied source tree, not a modified original capture. The upstream
voicing LAW excludes the parity reader and protection-unit code; the runtime
nevertheless enforces the original externally measured LAW/key pair. Collection
release and effective law-build are recorded separately, never relabelled.

The package supplies ordinary upstream provisioning/selfcheck/preview-switch
APIs and fails closed on missing source, numerical or hardware evidence. The
installer refuses pre-existing distribution files; rollback removes only
unchanged receipt-bound files after the graph/services are withdrawn.

The J716s protected-ceiling patch targets the pinned kernel. Exact machine and
J716 configuration are cached at probe; only the v2 interface may use the native
unity operating ceiling. Legacy/global caps, start admission, lease expiry and
safe-floor enforcement remain. Neither host tests nor an unbooted build confer
hardware qualification. The currently running old cap was observed to make the
supervisor withdraw; it was not bypassed with a readback or kernel-memory edit.

### Controller synchronous setup selection

The isolated legacy HFP setup patch uses the pinned kernel's existing
[enhanced-setup quirk](https://github.com/aurora-silicon/linux/blob/8ab10100a4c1c944797e3fc8345589aff57777df/include/net/bluetooth/hci.h)
and
[capability predicate](https://github.com/aurora-silicon/linux/blob/8ab10100a4c1c944797e3fc8345589aff57777df/include/net/bluetooth/hci_core.h).
The ordinary
[connection setup paths](https://github.com/aurora-silicon/linux/blob/8ab10100a4c1c944797e3fc8345589aff57777df/net/bluetooth/hci_conn.c)
retain codec/packet/latency and command-status handling. Only the experimental
T6050 Centauri controller probe sets the quirk; other Bluetooth drivers and
global HCI behavior are unchanged. Actual same-unit controller replies rejected
enhanced setup with status 0x11. That is evidence for trying the existing legacy
path, not proof that the controller implements it or that microphone audio works.

### Broad bringup ports

The full candidate ports [AVE #1747](https://github.com/aurora-silicon/linux/pull/1747),
[ANE #1746](https://github.com/aurora-silicon/linux/pull/1746) with firmware-requested
IPC bounds, [PHY #1745](https://github.com/aurora-silicon/linux/pull/1745),
[DP tunnel #1930](https://github.com/aurora-silicon/linux/pull/1930) and
[kept pools #1946](https://github.com/aurora-silicon/linux/pull/1946), with explicit
M5 identity-pin retention. The source/patch ledger and actual production-body
checks bind each adaptation. The J714s reports are source justification, not
local runtime qualification. AVE deliberately uses the tested compatibility
writer, not a guessed native-length extension. ANE's known upstream live pin
is unchanged; initial J716s acquisition is observation-only.

Thermal lane evidence derives from this unit's actual ADT. The preparatory
effort patch is not selected while PMP/report startup is unresolved, and fan
manual mode is not inferred from writable targets on another board. No private
wake stack, J714s routing table or best-effort camera privacy policy is imported.

### Exact ANE type metadata

The bound native J716s ADT provides non-placeholder little-endian type 0x220.
The pinned SoC DT documents it but omits the Linux property, and pinned Aura
copies preload reservations/IOVAs without the type. The J716s-only metadata
patch publishes exactly one big-endian u32; it changes no cold marker, owner,
clock, reset, reservation or GPIO. Kernel identity checks remain mandatory,
and observation-only startup remains required. Per-boot unit digests/receipts
stay private. Consumer checks cover missing, wrong and unreadable metadata
before TEXT access and preserve the existing reservation/pin/cold-state gates.

## Licensing and attribution

- Original integration scripts/docs are covered by the scoped root [`LICENSE`](../LICENSE).
- The pinned `omarchy-m5` revision included MIT terms, retained in `LICENSES/omarchy-m5-MIT.txt`. Later upstream licensing changes do not silently relicense that pinned copy.
- Aura combines inherited MIT code with GPL-2.0-only Aurora contributions. Its notice and both full license texts are retained in `LICENSES/`; the source headers/history determine the applicable portions.
- Kernel/UAPI headers and patches retain their SPDX identifiers and copyright lines. GPL and syscall-note obligations are not replaced by the orchestration license.
- Asahi, Aurora, Linux, Mesa, Arch Linux ARM, and Omarchy authorship remains in source headers and pinned upstream histories. Full fetched source trees retain their own licenses.
- The Warren installer is linked as a source reference rather than redistributed as an unused full script without established license terms.
- Apple firmware, personalized boot objects, disk images, and logo/font assets are not redistributed. Acquiring them locally does not grant this repository redistribution rights.
- Carried N1/firmware bring-up tools retain the same-board repository's MIT notice in `LICENSES/m5pro-linux-MIT.txt`; this does not relicense its kernel or m1n1 derivatives.

The repository release manifest covers published source files only. Per-machine target receipts, local build outputs, secrets, and binary firmware must remain excluded from Git.
