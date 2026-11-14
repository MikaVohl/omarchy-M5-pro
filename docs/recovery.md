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

### One-shot open-network kernel trial

`scripts/stage-open-wifi.py` stages a separate trial using the installed
`aura-try` mechanism. It never replaces `aurora/boot.bin`, `aurora/root.bin`,
the normal root dependencies, or the rescue payload/dependencies. It does not
change partitions, enrolled Stage 1, Apple security policy, or the baseline image
identity. Staging and arming require explicit risk acknowledgement; this does
not replace the backup and recovery prerequisites above.

Run the default read-only target check first, then stage and arm separately:

```sh
sudo python3 scripts/stage-open-wifi.py
sudo python3 scripts/stage-open-wifi.py --stage --acknowledge-risk
sudo python3 scripts/stage-open-wifi.py --arm --acknowledge-risk
```

The default check writes only temporary project-workspace files. It checks the
actual installed guard with a zero-fuzz patch and `--check-only`, verifies
baseline root/rescue hashes, the candidate build, and the marker's raw extent.
Staging installs the new release's modules, separate ESP files, a root-owned
trial receipt, and narrowly extended runtime validation. All existing installed
guard checks remain. The original guard is retained privately under
`work/build/open-wifi-trial/baseline-guard.py`. Arming writes only the existing
marker in place, after all staged hashes and baseline checks pass; no command
above reboots.

Cancel before reboot, without removing any preserved boot files:

```sh
sudo python3 scripts/stage-open-wifi.py --cancel
```

Aura spends `armed\n<path>\n` before loading the trial. After a failed trial,
another Linux boot should therefore use the original configured kernel. If the
machine hangs, shut it down with the power button, then boot Linux again. Holding
power from shutdown opens Apple's Startup Options, where retained macOS is an
independent option. These are recovery paths, not a guarantee against firmware
or storage failures. The running loader reports the pinned source revision with
custom build suffixes; source inspection and an existing spent marker are not a
new physical qualification of its fallback.

After a successful test boot, check `uname -r` for
`7.3.0-j716s-8ab10100a4c1-open-wifi` and confirm
`j716s-boot-bind.service` succeeded before testing Wi-Fi. A later Linux boot
returns to the baseline unless the trial is deliberately rearmed. Do not make
the experimental kernel permanent merely because it boots.

### Selecting the tested kernel by default

After a successful physical trial and explicit owner authorization:

```sh
sudo python3 scripts/select-open-wifi.py
sudo python3 scripts/select-open-wifi.py --activate
```

Activation validates the running candidate, installed guard, root/ESP identities,
original root/rescue closure, and candidate files/modules. It installs the
receipt-bound persistent guard and saves the previous installed guard/helper
privately under `/var/lib/j716s/open-wifi-default-backups/`. A separate root-owned
`/etc/j716s/open-wifi-default.json` binds the exact candidate receipt and payload.
Only after those files are durable does activation atomically publish the tested
payload as `aurora/boot.bin`. It does not change Stage 1, partitions, firmware,
baseline image identities, `aurora/root.bin`, or the rescue closure.

This changes the default across reboots; there is **no automatic one-shot
return to the original kernel**. Roll back explicitly:

```sh
sudo python3 scripts/select-open-wifi.py --rollback
```

Rollback verifies and republishes the preserved `aurora/root.bin`. It does not
require intact candidate modules or a successful candidate guard. The next
Linux boot then uses the original kernel; the currently running kernel is not
changed. Neither activation nor rollback issues a reboot.

If Linux cannot run the rollback command, use retained macOS/Startup Options
and identify the ESP from its recorded partition UUID before restoring
`aurora/root.bin` to `aurora/boot.bin`. Do not repartition, reenroll, or change
Apple security policy to switch these payloads.



See [setup](setup.md) for the exact current CLI sequence and [console capture](console.md) for independent boot evidence.
