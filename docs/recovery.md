# Recovery and failure handling

## Retain recovery before installing

Keep a verified current backup, a bootable firmware-matched macOS installation, and physical access to Startup Options/paired recovery. Do not delete Apple's ISC or system Recovery partitions. A Linux RAM rescue must actually boot on your target before treating it as a fallback.

The persistent deployment preserves a separate rescue payload at `aurora/rescue.bin` with its hash-bound dependencies under `aurora/rescue/`. Persistent-root dependencies live under `aurora/root/`; `aurora/boot.bin` is the stable Stage 1 selection path. The runtime guard verifies the actual root and ESP identities and their dependency hashes before owner setup or the desktop.

## If Linux does not boot

1. Do not repeatedly rearm, reenroll, or rewrite disks. A failed early boot does not establish a partition or security-policy problem.
2. Shut down. Hold the power button for Startup Options and select the retained macOS installation. Log in physically. Preserve the failed build/deployment journal locally.
3. Resolve the stub, ESP, source container, and Linux partition from their own current receipts. Device numbers and volume names are not identities. Do not use another machine's receipt.
4. Inspect the ESP closure and raw/readback result against those receipts. Do not rerun fresh partition preparation or persistent deployment against an already formatted Linux partition.
5. Use the separately qualified RAM rescue and independent KIS capture to locate the failure. A failed identity/dependency check is a blocker to investigate, not a reason to bypass the guard.

## Interrupted destructive operations

Preparation journals record the baseline protected partitions and each newly created GUID. A partial partition/stub attempt must be inspected against its journal; only an explicitly supported guarded continuation is appropriate. A persistent deploy is fresh-target-only and does not offer an overwrite/reinstall recovery mode.

The active boot payload is published last, after the root write/readback and dependency verification. If a write was interrupted, do not assume either the pending or active path is safe. Keep all receipts and compare the actual files before selecting a payload.

Changing a previously enrolled raw Stage 1, changing boot security, deleting volumes, restoring a whole disk, or repartitioning is outside ordinary desktop recovery. Those actions require a new, reviewed plan and explicit owner approval.

## Kernel and package updates

The hardware boot stack is deliberately outside ordinary package ownership. Pacman hooks block packages from taking ownership of protected paths or removing the custom stack. Do not disable those hooks to install a generic kernel or bootloader. A new kernel, firmware, Mesa, or Aura version needs a matched rebuild, fresh manifests, and physical rescue qualification.

Speakers remain disabled until a board-specific audio/speaker-safety profile is qualified. A desktop boot does not qualify audio or suspend.

See [setup](setup.md) for the exact current CLI sequence and [console capture](console.md) for independent boot evidence.
