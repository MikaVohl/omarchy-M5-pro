# Independent KIS console capture

SSH is useful after macOS or Linux networking starts. USB Ethernet exposed by macOS is also an OS-level network path. Neither is an independent early-boot console or a reset controller.

The qualified bootloader retains KIS/DockChannel, and the rescue/persistent DTB reserves USB0 and its two DARTs so Linux does not claim the debug transport. Keep the supported cable/port and a Linux receiver attached before a controlled boot.

## Receiver

Use [AsahiLinux/kisd](https://github.com/AsahiLinux/kisd), the Linux DebugUSB receiver. Release v1.1.0 resolves to commit `555c3b0d0e39e8a5ffaaa70d54452f263ac4df21`. Its README documents M5 Pro/T6050 protocol 4.00 and base `0x2c8d00000` as verified. Build with Cargo and the supplied lockfile; receiver setup is separate from installing Linux on the Mac.

```sh
git clone --no-checkout https://github.com/AsahiLinux/kisd /tmp/j716s-kisd-source
git -C /tmp/j716s-kisd-source checkout --detach 555c3b0d0e39e8a5ffaaa70d54452f263ac4df21
cargo build --locked --release --manifest-path /tmp/j716s-kisd-source/Cargo.toml
# On an authorized receiver, only if no receiver daemon is already running:
sudo /tmp/j716s-kisd-source/target/release/kisd --base 0x2c8d00000
```

Running kisd as root creates `/dev/m1n1`. Non-root setup requires the upstream udev rule and USB permissions, and uses the PTY path reported by kisd instead. Do not replace an already running receiver daemon or change global udev rules without the receiver owner's approval.

With kisd running and the target in DebugUSB mode:

```sh
python3 scripts/capture-console.py --device /dev/m1n1 \
  --output /tmp/j716s-boot.log --seconds 180
```

The capture utility opens the local PTY read-only, sends no proxy/console commands, and writes a new mode-0600 log. With non-root kisd, replace the device argument with its reported `/dev/pts/N`. Logs may contain per-unit data; retain locally and do not commit them.

Transport availability must be checked before the boot. The qualified loader can retain a debug path, but it does not make an arbitrary cable, USB port, receiver permission, or firmware mode work. Upstream `tuxvdmtool` DebugUSB switching is a separate target-control action, not something the read-only capture script invokes.

## Interpreting evidence

- Receiver attachment or an Aura banner proves only the transport/stage reached, not Linux entry.
- A kernel log proves only the named kernel stage, not successful persistent-root or GUI setup.
- A frame or compositor configuration check is not proof of native rendering; use the included GPU preflight and actual authenticated session.
- Preserve failed-trial logs before changing a payload. Do not blindly repeat enrollment/security changes to diagnose an early kernel hang.
