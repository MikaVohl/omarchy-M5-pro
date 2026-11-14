#!/bin/bash
# Recovery helper: mount exactly the recorded stub volumes and ESP by UUID before step2.sh.
# Uses only recoveryOS tools (bash 3.2, diskutil, plutil, mktemp). Mounts nothing else.
set -u
export PATH=/usr/bin:/usr/sbin:/bin:/sbin
MODE=${1:---check}
[ $# -le 1 ] || { echo 'usage: bash mount-targets.sh [--check | --apply]'; exit 2; }
case "$MODE" in --check|--apply) ;; *) echo 'usage: bash mount-targets.sh [--check | --apply]'; exit 2 ;; esac
case "$0" in */*) RES=$(cd "${0%/*}" && pwd -P) ;; *) RES=$(pwd -P) ;; esac
BINDING="$RES/target.plist"
[ -f "$BINDING" ] && [ ! -L "$BINDING" ] || { echo "REFUSED: target binding missing/symlink"; exit 1; }
expected=$(plutil -extract MOUNT_MD5 raw -o - "$BINDING") || exit 1
[ "$(/sbin/md5 -q "$RES/mount-targets.sh")" = "$expected" ] || { echo "REFUSED: mount helper differs"; exit 1; }
TMP=$(mktemp /tmp/mount-targets.XXXXXX) || exit 1
STUB=$(plutil -extract STUB_PARTUUID raw -o - "$BINDING") || exit 1
SOURCE=$(plutil -extract SOURCE_PARTUUID raw -o - "$BINDING") || exit 1
GROUP=$(plutil -extract GROUP raw -o - "$BINDING") || exit 1
diskutil info -plist "$STUB" > "$TMP" || exit 1
[ "$(plutil -extract Content raw -o - "$TMP")" = Apple_APFS ] || exit 1
WHOLE=$(plutil -extract ParentWholeDisk raw -o - "$TMP") || exit 1
diskutil info -plist "$SOURCE" > "$TMP" || exit 1
[ "$STUB" != "$SOURCE" ] && [ "$(plutil -extract ParentWholeDisk raw -o - "$TMP")" = "$WHOLE" ] \
    && [ "$(plutil -extract Content raw -o - "$TMP")" = Apple_APFS ] || exit 1
diskutil info -plist "$WHOLE" > "$TMP" || exit 1
[ "$(plutil -extract Internal raw -o - "$TMP")" = true ] && [ "$(plutil -extract SolidState raw -o - "$TMP")" = true ] || exit 1
SYSTEM=$(plutil -extract SYSTEM_UUID raw -o - "$BINDING") || exit 1
diskutil info -plist "$SYSTEM" > "$TMP" || exit 1
CT=$(plutil -extract APFSContainerReference raw -o - "$TMP") || exit 1
APFS="$TMP.apfs"
diskutil apfs list -plist > "$APFS" || exit 1
rc=0
for role in ESP System Data Preboot Recovery; do
    case "$role" in ESP) key=ESP_PARTUUID ;; System) key=SYSTEM_UUID ;; *) key=$(printf '%s' "$role" | tr 'a-z' 'A-Z')_UUID ;; esac
    uuid=$(plutil -extract "$key" raw -o - "$BINDING") || { rc=1; break; }
    [[ "$uuid" =~ ^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$ ]] || { echo "REFUSED: invalid $role UUID"; rc=1; break; }
    if ! diskutil info -plist "$uuid" > "$TMP"; then echo "MISSING $role $uuid"; rc=1; continue; fi
    if [ "$role" = ESP ]; then
        [ "$(plutil -extract DiskUUID raw -o - "$TMP")" = "$uuid" ] \
            && [ "$(plutil -extract Content raw -o - "$TMP")" = EFI ] \
            && [ "$(plutil -extract ParentWholeDisk raw -o - "$TMP")" = "$WHOLE" ] || { rc=1; break; }
    else
        [ "$(plutil -extract VolumeUUID raw -o - "$TMP")" = "$uuid" ] \
            && [ "$(plutil -extract APFSContainerReference raw -o - "$TMP")" = "$CT" ] || { rc=1; break; }
        ci=0 rolehits=0
        while plutil -extract "Containers.$ci.ContainerReference" raw -o - "$APFS" >/dev/null 2>&1; do
            vi=0
            while vu=$(plutil -extract "Containers.$ci.Volumes.$vi.APFSVolumeUUID" raw -o - "$APFS" 2>/dev/null); do
                if [ "$vu" = "$uuid" ]; then
                    [ "$(plutil -extract "Containers.$ci.Volumes.$vi.Roles.0" raw -o - "$APFS")" = "$role" ] || { rc=1; break; }
                    rolehits=$((rolehits + 1))
                fi
                vi=$((vi + 1))
            done
            ci=$((ci + 1))
        done
        [ "$rolehits" = 1 ] || { rc=1; break; }
        case "$role" in System|Data) [ "$(plutil -extract APFSVolumeGroupID raw -o - "$TMP")" = "$GROUP" ] || { rc=1; break; } ;; esac
    fi
    mp=$(plutil -extract MountPoint raw -o - "$TMP" 2>/dev/null || true)
    if [ -z "$mp" ] && [ "$MODE" = --apply ]; then
        if [ "$role" = ESP ]; then
            # step2 only reads the ESP. Read-only avoids leaving the FAT dirty across a restart.
            # DiskArbitration may not mount this 4K-sector FAT; FSKit msdos does (root only).
            dev=$(plutil -extract DeviceIdentifier raw -o - "$TMP")
            if ! diskutil mount readOnly "$uuid" > /dev/null 2>&1; then
                mounted=no
                if [ "$(id -u)" = 0 ] && { [ -d /Volumes/OMARCHY-ESP ] || mkdir /Volumes/OMARCHY-ESP; }; then
                    for ro in "-r" "-o ro"; do
                        if mount -F $ro -t msdos "/dev/$dev" /Volumes/OMARCHY-ESP 2>/dev/null; then mounted=yes; break; fi
                    done
                fi
                [ "$mounted" = yes ] || { echo "FAILED to mount $role $uuid"; rc=1; continue; }
            fi
        elif ! diskutil mount readOnly "$uuid" > /dev/null; then
            echo "FAILED to mount $role $uuid"; rc=1; continue
        fi
        diskutil info -plist "$uuid" > "$TMP"
        mp=$(plutil -extract MountPoint raw -o - "$TMP" 2>/dev/null || true)
    fi
    echo "$role $(plutil -extract DeviceIdentifier raw -o - "$TMP") -> ${mp:-NOT MOUNTED}"
    [ -n "$mp" ] || rc=1
done
rm -f "$TMP" "$APFS"
[ $rc = 0 ] && echo 'All targets are mounted. Next: bash step2.sh --check' || echo 'Some targets are not mounted; --check never mounts. Run mount-targets.sh --apply only for these recorded UUIDs.'
exit $rc
