#!/bin/busybox sh
# J716s persistent-root init. No disk selection by device number or display name.
# Root is mounted read-only; systemd owns fsck, remount and first-boot provisioning.
export PATH=/bin:/sbin:/usr/bin:/usr/sbin

refuse() {
    printf '\nJ716s root boot refused: %s\nNo other filesystem will be mounted. Use the retained rescue payload or macOS.\n' "$*" >&3
    while true; do
        /bin/busybox sh <&3 >&3 2>&1
        /bin/busybox sleep 1
    done
}

valid_uuid() {
    case "$1" in
        ????????-????-????-????-????????????) ;;
        *) return 1 ;;
    esac
    hex=$(printf '%s' "$1" | /bin/busybox tr -d '-')
    case "$hex" in *[!0-9a-fA-F]*|'') return 1 ;; esac
    [ "${#hex}" = 32 ]
}

parse_bootargs() {
    ROOT_GUID= ESP_GUID= ROOT_FSUUID=
    root_count=0 esp_count=0 fs_count=0
    for arg in $1; do
        case "$arg" in
            root=PARTUUID=*) ROOT_GUID=${arg#root=PARTUUID=}; root_count=$((root_count + 1)) ;;
            root=*) return 1 ;;
            j716s.esp-partuuid=*) ESP_GUID=${arg#j716s.esp-partuuid=}; esp_count=$((esp_count + 1)) ;;
            j716s.root-fsuuid=*) ROOT_FSUUID=${arg#j716s.root-fsuuid=}; fs_count=$((fs_count + 1)) ;;
        esac
    done
    [ "$root_count" = 1 ] && [ "$esp_count" = 1 ] && [ "$fs_count" = 1 ] || return 1
    valid_uuid "$ROOT_GUID" && valid_uuid "$ESP_GUID" && valid_uuid "$ROOT_FSUUID" || return 1
    ROOT_GUID=$(printf '%s' "$ROOT_GUID" | /bin/busybox tr 'A-F' 'a-f')
    ESP_GUID=$(printf '%s' "$ESP_GUID" | /bin/busybox tr 'A-F' 'a-f')
    ROOT_FSUUID=$(printf '%s' "$ROOT_FSUUID" | /bin/busybox tr 'A-F' 'a-f')
    [ "$ROOT_GUID" != "$ESP_GUID" ]
}

# This diagnostic exercises the same parser without mounts or privilege changes.
if [ "${1:-}" = --check-arguments ]; then
    [ "$#" = 2 ] && parse_bootargs "$2" || exit 1
    printf 'root=%s\nesp=%s\nfilesystem=%s\n' "$ROOT_GUID" "$ESP_GUID" "$ROOT_FSUUID"
    exit 0
fi

[ "$#" = 0 ] || exit 2
/bin/busybox mount -t devtmpfs devtmpfs /dev || exit 1
exec 3<>/dev/console || exit 1
/bin/busybox mount -t proc proc /proc || refuse 'proc mount failed'
/bin/busybox mount -t sysfs sysfs /sys || refuse 'sysfs mount failed'
/bin/busybox mkdir -p /run /newroot
/bin/busybox mount -t tmpfs tmpfs /run || refuse 'run mount failed'
/bin/busybox tr '\000' '\n' </proc/device-tree/compatible | /bin/busybox grep -qx apple,j716s || refuse 'not the J716s board'
parse_bootargs "$(/bin/busybox cat /proc/cmdline)" || refuse 'missing, duplicate or invalid root/ESP/filesystem UUID'

# sysfs PARTUUID comes from the GPT parser, not a label or FAT volume UUID.
remaining=60
while true; do
    matches=0 ROOT_DEVICE=
    for entry in /sys/class/block/*/uevent; do
        [ -f "$entry" ] || continue
        device= guid=
        while IFS='=' read -r key value; do
            case "$key" in DEVNAME) device=$value ;; PARTUUID) guid=$value ;; esac
        done <"$entry"
        guid=$(printf '%s' "$guid" | /bin/busybox tr 'A-F' 'a-f')
        if [ "$guid" = "$ROOT_GUID" ]; then
            case "$device" in ''|*/*|*..*) refuse 'unsafe block device name' ;; esac
            ROOT_DEVICE=/dev/$device
            matches=$((matches + 1))
        fi
    done
    [ "$matches" -le 1 ] || refuse 'duplicate root partition GUID'
    [ "$matches" = 1 ] && [ -b "$ROOT_DEVICE" ] && break
    [ "$remaining" -gt 0 ] || refuse 'root partition did not appear'
    remaining=$((remaining - 1))
    /bin/busybox sleep 1
done

# ext4 superblock: magic at 1024+56 and filesystem UUID at 1024+104.
magic=$(/bin/busybox od -An -tx1 -j 1080 -N 2 "$ROOT_DEVICE" | /bin/busybox tr -d ' \n')
[ "$magic" = 53ef ] || refuse 'selected partition is not ext4'
actual=$(/bin/busybox od -An -tx1 -j 1128 -N 16 "$ROOT_DEVICE" | /bin/busybox tr -d ' \n')
expected=$(printf '%s' "$ROOT_FSUUID" | /bin/busybox tr -d '-')
[ "$actual" = "$expected" ] || refuse 'selected ext4 filesystem differs from the prepared image'
/bin/busybox mount -t ext4 -o ro "$ROOT_DEVICE" /newroot || refuse 'read-only root mount failed'
[ -x /newroot/sbin/init ] && [ -f /newroot/usr/share/j716s/image-identity.json ] || refuse 'root lacks prepared init/image identity'
for tree in dev proc sys run; do
    [ -d "/newroot/$tree" ] && [ ! -L "/newroot/$tree" ] || refuse "root has no safe $tree mount point"
done
for tree in dev proc sys run; do
    /bin/busybox mount --move "/$tree" "/newroot/$tree" || refuse "cannot move $tree into root"
done
exec 3>&-
exec /bin/busybox switch_root /newroot /sbin/init
