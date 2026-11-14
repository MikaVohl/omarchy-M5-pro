# Omarchy on J716s / M5 Pro

Reproduction sources for a working **Mac17,8 / J716s / T6050 M5 Pro** installation: persistent Arch Linux ARM + Omarchy, native GPU/DCP, password-login SDDM, and full-notch **3456×2234 at 120 Hz**.

This is an experimental, exact-firmware bring-up. It is not a general Apple Silicon installer. **Back up first. Partition preparation, paired-recovery enrollment, and persistent deployment are destructive or security-sensitive operations.** Read the complete sequence before running any `--apply` command.

## Start here

1. [Build and installation sequence](docs/setup.md)
2. [Recovery and rollback](docs/recovery.md)
3. [Observed hardware results and limits](docs/hardware-status.md)
4. [Sources, licenses, and build reproducibility](docs/provenance.md)

## What is included

- Source-pinned kernel and Aura build recipes, including the qualified firmware and DCP handoff patches.
- Board-matched firmware preparation from your own verified Apple IPSW; no firmware is redistributed.
- RAM rescue and root initramfs tooling.
- Explicit-identity macOS preflight, stub/ESP/Linux preparation, raw Stage 1 preparation, and paired-1TR enrollment utilities.
- Signed Arch Linux ARM base acquisition, isolated root-image construction, private native Mesa, and Omarchy provisioning.
- GPT/ext4 identity and boot-dependency guards, full raw-image readback, publication-last deployment, owner setup, and the working SDDM/full-notch fixes.
- Public package-verification keys and deterministic metadata safety tests.

## What is deliberately excluded

Apple firmware, IPSWs, installers, generated boot objects, disk images, module-signing private keys, passwords, SSH keys, original per-machine UUID receipts, private diagnostic logs, and personal screenshots. Build and target receipts are generated locally and must not be committed.

## Scope

The required pairing is **macOS 27.3 beta 1 / 26C5035r / mBoot-20457.60.65.0.1**. Source revisions and public input hashes are in [`profiles/j716s.json`](profiles/j716s.json). A different firmware build or board needs separate engineering and qualification; do not weaken checks to force it through.

The original persistent desktop and full-notch restart worked. Audio, suspend/resume, external displays, camera, Touch ID, and daily-use stability remain unqualified. Speakers stay disabled. Newly rebuilt artifacts still require physical rescue qualification on their target.

The bringup audit additionally verified short hardware H.264 decode and Moonlight VA-API startup. Speaker-route guarding is fixed; an isolated Bluetooth/GPU-diagnostics candidate is selected for the next boot, with prior-default rollback. See [hardware status](docs/hardware-status.md) and [verification](docs/verification.md) for the exact limits.

Source provenance includes [rolotrealanis98/m5pro-linux](https://github.com/rolotrealanis98/m5pro-linux), [Aurora Silicon](https://github.com/aurora-silicon), [betizzel/omarchy-m5](https://github.com/betizzel/omarchy-m5), the [Asahi Linux project](https://asahilinux.org/), [Arch Linux ARM](https://archlinuxarm.org/), and [Omarchy](https://omarchy.org/). Joshua Warren's [M5 Max account](https://joshuawarren.com/blog/omarchy-on-the-m5-max-macbook-pro/) informed the desktop integration; its K114c hardware data was not transplanted.
