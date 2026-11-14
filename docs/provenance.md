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
