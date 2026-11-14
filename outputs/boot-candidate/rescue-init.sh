#!/bin/busybox sh
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
/bin/busybox mount -t proc proc /proc
/bin/busybox mount -t sysfs sysfs /sys
/bin/busybox mount -t devtmpfs devtmpfs /dev
/bin/busybox mkdir -p /dev/pts /run /tmp
/bin/busybox mount -t devpts devpts /dev/pts
/bin/busybox mount -t tmpfs -o mode=0755 tmpfs /run
/bin/busybox mount -t tmpfs -o mode=1777 tmpfs /tmp
/bin/busybox mdev -s
if [ -w /proc/sys/kernel/hotplug ]; then
  printf '/sbin/mdev\n' >/proc/sys/kernel/hotplug
fi
printf '\nJ716s RAM recovery: no automatic block-device mounts or disk writes.\n'
printf 'Freshly rebuilt kernel/loader/userspace bytes are NOT hardware-qualified.\n'
/bin/busybox uname -a
printf '\nStorage enumeration (read-only):\n'
/bin/busybox cat /proc/partitions
/bin/busybox blkid
printf '\nInput enumeration:\n'
/bin/busybox cat /proc/bus/input/devices
printf '\nNetwork interfaces:\n'
/bin/busybox ip link
printf '\nDRM connector modes (enumeration is not scanout qualification):\n'
for modes in /sys/class/drm/card*-*/modes; do
  [ -f "$modes" ] || continue
  printf '%s\n' "$modes"
  /bin/busybox cat "$modes"
done
# This optional command binds only RAM/pseudo filesystems into the closure.
# It never mounts a target block device and makes no persistent accounts.
if [ -x /desktop/usr/local/bin/j716s-start-desktop ]; then
  /bin/busybox cat >/usr/bin/start-desktop <<'DESKTOP'
#!/bin/busybox sh
set -eu
for directory in dev proc sys run; do
  /bin/busybox mkdir -p "/desktop/$directory"
  /bin/busybox mountpoint -q "/desktop/$directory" || /bin/busybox mount --bind "/$directory" "/desktop/$directory"
done
/bin/busybox mkdir -p /run/user/1000
/bin/busybox chown 1000:1000 /run/user/1000
/bin/busybox chmod 0700 /run/user/1000
if [ ! -S /run/seatd.sock ]; then
  /bin/busybox chroot /desktop /usr/bin/seatd -s /run/seatd.sock -g video >/run/seatd.log 2>&1 &
  attempts=0
  while [ ! -S /run/seatd.sock ]; do
    attempts=$((attempts + 1))
    [ "$attempts" -lt 50 ] || { /bin/busybox cat /run/seatd.log; exit 1; }
    /bin/busybox sleep 0.1
  done
fi
/bin/busybox chroot /desktop /usr/lib/systemd/systemd-udevd --daemon
/bin/busybox chroot /desktop /usr/bin/udevadm trigger --action=add
/bin/busybox chroot /desktop /usr/bin/udevadm settle
exec /bin/busybox chroot /desktop /usr/bin/setpriv --reuid=1000 --regid=1000 --init-groups /usr/local/bin/j716s-start-desktop
DESKTOP
  /bin/busybox chmod 0755 /usr/bin/start-desktop
  printf '\nOptional native RAM desktop: start-desktop\n'
  printf 'Native G17S GPU preflight must pass; there is no software-rendering fallback.\n'
fi
printf '\nExit returns to this recovery shell; it does not reboot.\n'
while true; do
  /bin/busybox sh -l </dev/console >/dev/console 2>&1
  /bin/busybox sleep 1
done
