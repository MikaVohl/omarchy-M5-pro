#!/bin/bash
# SPDX-License-Identifier: MIT
# Runs only in the explicitly selected stub's paired 1TR. Default is --check.
# No Python, implicit mount, policy changes, automatic retry, shutdown or reboot.
set -euo pipefail
# The receipt-bound raw enrollment tool resolves several binaries through PATH.
export PATH=/usr/bin:/usr/sbin:/bin:/sbin
usage() {
    printf '%s\n' 'usage: bash step2.sh [--check | --apply]' \
        '  --check  default; verify real target/group/ESP, copies, firmware and pinned upstream checks' \
        '  --apply  root-only; fresh backup confirmation and owner authentication, raw enrollment' \
        '           clear install-media marker ONLY after upstream returns success' \
        'Kernel CTRR must first be disabled for this stub by its owner in paired 1TR. No auto reboot.'
}
MODE=check
case "${1:---check}" in
    --check) ;;
    --apply) MODE=apply ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
esac
[ $# -le 1 ] || { usage; exit 2; }
fail() { printf '\nREFUSED: %s\nInstall-media state retained; no automatic retries, bless or reboot.\n' "$*" >&2; exit 1; }
for tool in /usr/sbin/diskutil /usr/bin/plutil /sbin/md5 /usr/bin/kmutil /usr/bin/bputil /usr/sbin/ioreg /usr/bin/tr /usr/bin/sed /usr/sbin/sysctl /usr/bin/id /usr/bin/mktemp /bin/bash /usr/bin/tee /bin/date /sbin/mount /bin/mv /bin/sync /usr/bin/stat /usr/bin/pmset; do
    [ -x "$tool" ] || fail "required Recovery tool missing: $tool"
done
[ "$MODE" = check ] || [ "$(/usr/bin/id -u)" = 0 ] || fail '--apply requires sudo/root'
[ "$(/usr/sbin/sysctl -n hw.model)" = Mac17,8 ] || fail 'Recovery requires J716s/Mac17,8'
case "$0" in
    */*) RES=$(cd "${0%/*}" && pwd -P) || fail 'cannot locate resources' ;;
    *) RES=$(pwd -P) || fail 'cannot locate resources' ;;
esac
SYS=$(cd "$RES/../../../" && pwd -P) || fail 'cannot locate mounted System volume'
TARGET="$RES/target.plist"
[ -f "$TARGET" ] && [ ! -L "$TARGET" ] || fail 'target binding missing/symlink'
# plist values are data. Never source a writable binding as shell code.
p() { /usr/bin/plutil -extract "$2" raw -o - "$1" 2>/dev/null; }
u() { printf '%s' "$1" | /usr/bin/tr 'a-f' 'A-F'; }
uuid_ok() { [[ "$1" =~ ^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$ ]]; }
for key in SYSTEM_UUID STUB_PARTUUID STUB_CONTAINER_UUID GROUP SOURCE_PARTUUID SOURCE_CONTAINER_UUID SOURCE_GROUP SOURCE_SYSTEM_UUID SOURCE_DATA_UUID SOURCE_PREBOOT_UUID SOURCE_RECOVERY_UUID ESP_PARTUUID DATA_UUID PREBOOT_UUID RECOVERY_UUID; do
    value=$(p "$TARGET" "$key") || fail "binding field missing: $key"
    uuid_ok "$value" || fail "malformed binding UUID: $key"
    printf -v "$key" '%s' "$value"
done
STAGE2_PATH=$(p "$TARGET" STAGE2_PATH) || fail 'Stage 2 path missing'
case "$STAGE2_PATH" in ''|/*|*\\*|*';'*|*/../*|../*|*/..|*/./*|./*|*/.|*//*|*/) fail 'unsafe ESP relative path' ;; esac
md5_ok() {
    [ -f "$1" ] && [ ! -L "$1" ] && [[ "$2" =~ ^[0-9a-f]{32}$ ]] && [ "$(/sbin/md5 -q "$1")" = "$2" ]
}
safe_file() {
    local root="$1" relative="$2" current part
    local parts=()
    case "$relative" in ''|/*|../*|*/../*|*/..|./*|*/./*|*/.|*//*|*/) fail 'unsafe relative asset path' ;; esac
    IFS=/ read -r -a parts <<< "$relative"
    current="$root"
    for part in "${parts[@]}"; do
        current="$current/$part"
        [ ! -L "$current" ] || fail "symlink in target asset path: $current"
    done
    [ -f "$current" ] || fail "required target asset missing: $current"
}
# Receipt-recorded relative symlink: non-symlink parents and the exact recorded target.
safe_link() {
    local root="$1" relative="$2" target="$3" current part last
    local parts=()
    case "$relative" in ''|/*|../*|*/../*|*/..|./*|*/./*|*/.|*//*|*/) fail 'unsafe relative link path' ;; esac
    case "$target" in ''|/*) fail "recorded link target must be relative: $relative" ;; esac
    IFS=/ read -r -a parts <<< "$relative"
    last=$((${#parts[@]} - 1))
    current="$root"
    for part in "${parts[@]:0:$last}"; do
        current="$current/$part"
        [ ! -L "$current" ] && [ -d "$current" ] || fail "unsafe link parent: $current"
    done
    current="$current/${parts[$last]}"
    [ -L "$current" ] && [ "$(/usr/bin/stat -f %Y "$current")" = "$target" ] || fail "paired symlink differs: $current"
}
check_copy() { local expected; expected=$(p "$TARGET" "$2") || fail "missing checksum $2"; md5_ok "$RES/$1" "$expected" || fail "copied resource differs: $1"; }
check_copy boot.bin BOOT_MD5
check_copy enroll-stage1.sh ENROLL_MD5
check_copy step2.sh STEP2_MD5
check_copy stage1-receipt.json STAGE1_RECEIPT_MD5
check_copy stub-receipt.json STUB_RECEIPT_MD5
check_copy finish-install-derivative.json PROVENANCE_MD5
check_copy assets.plist ASSETS_MD5
check_copy mount-targets.sh MOUNT_MD5
# Temporary diagnostics are outside the target. Upstream --check also logs here.
TMP=$(/usr/bin/mktemp -d /tmp/finish-j716s.XXXXXX) || fail 'cannot create diagnostics directory'
# Retain diagnostics on failure. No EXIT trap touches target media state.
APFS="$TMP/apfs.plist"
/usr/sbin/diskutil apfs list -plist > "$APFS" || fail 'APFS inventory failed'
info() { /usr/sbin/diskutil info -plist "$1" > "$TMP/info.plist" || fail "diskutil info failed: $1"; }
# Locate by explicit System UUID, never names or a global role.
ci=0 hits=0 CTIDX=''
while p "$APFS" "Containers.$ci.ContainerReference" >/dev/null; do
    vi=0
    while dev=$(p "$APFS" "Containers.$ci.Volumes.$vi.DeviceIdentifier"); do
        voluuid=$(p "$APFS" "Containers.$ci.Volumes.$vi.APFSVolumeUUID") || fail 'volume UUID absent'
        if [ "$(u "$voluuid")" = "$SYSTEM_UUID" ]; then
            hits=$((hits + 1)); CTIDX=$ci; SYSDEV=$dev
        fi
        vi=$((vi + 1))
    done
    ci=$((ci + 1))
done
[ "$hits" = 1 ] || fail 'System UUID must select exactly one APFS volume'
CT=$(p "$APFS" "Containers.$CTIDX.ContainerReference")
[ "$(u "$(p "$APFS" "Containers.$CTIDX.APFSContainerUUID")")" = "$STUB_CONTAINER_UUID" ] || fail 'stub container UUID changed'
STORE=$(p "$APFS" "Containers.$CTIDX.PhysicalStores.0.DeviceIdentifier") || fail 'stub physical store absent'
! p "$APFS" "Containers.$CTIDX.PhysicalStores.1.DeviceIdentifier" >/dev/null || fail 'multiple stub stores'
[ "$(u "$(p "$APFS" "Containers.$CTIDX.PhysicalStores.0.DiskUUID")")" = "$STUB_PARTUUID" ] || fail 'stub physical UUID mismatch'
size=$(p "$APFS" "Containers.$CTIDX.CapacityCeiling")
[[ "$size" =~ ^[0-9]+$ ]] && [ "$size" -ge 5983222784 ] && [ "$size" -le 6016777216 ] || fail 'stub is not approximately 6 GB'
info "$STORE"
[ "$(u "$(p "$TMP/info.plist" DiskUUID)")" = "$STUB_PARTUUID" ] && [ "$(p "$TMP/info.plist" Content)" = Apple_APFS ] || fail 'stub is not selected ordinary APFS partition (ISC/Recovery refused)'
WHOLE=$(p "$TMP/info.plist" ParentWholeDisk)
size=$(p "$TMP/info.plist" TotalSize)
[[ "$size" =~ ^[0-9]+$ ]] && [ "$size" -ge 5983222784 ] && [ "$size" -le 6016777216 ] || fail 'stub physical size mismatch'
info "$WHOLE"
[ "$(p "$TMP/info.plist" Internal)" = true ] && [ "$(p "$TMP/info.plist" SolidState)" = true ] || fail 'stub not on internal SSD'
# Locate retained source independently and bind its physical disk/container UUID.
ci=0 sourcehits=0
while p "$APFS" "Containers.$ci.ContainerReference" >/dev/null; do
    pi=0
    while dev=$(p "$APFS" "Containers.$ci.PhysicalStores.$pi.DeviceIdentifier"); do
        partuuid=$(p "$APFS" "Containers.$ci.PhysicalStores.$pi.DiskUUID")
        if [ "$(u "$partuuid")" = "$SOURCE_PARTUUID" ]; then
            sourcehits=$((sourcehits + 1))
            SOURCEIDX=$ci
            [ "$ci" != "$CTIDX" ] && [ "$(u "$(p "$APFS" "Containers.$ci.APFSContainerUUID")")" = "$SOURCE_CONTAINER_UUID" ] || fail 'stub/source container collision or source changed'
            ! p "$APFS" "Containers.$ci.PhysicalStores.1.DeviceIdentifier" >/dev/null || fail 'retained source has multiple stores'
            info "$dev"
            [ "$(p "$TMP/info.plist" ParentWholeDisk)" = "$WHOLE" ] && [ "$(p "$TMP/info.plist" Content)" = Apple_APFS ] || fail 'retained source disk/type differs'
        fi
        pi=$((pi + 1))
    done
    ci=$((ci + 1))
done
[ "$sourcehits" = 1 ] || fail 'retained source partition missing or ambiguous'
[ "$SOURCE_PARTUUID" != "$STUB_PARTUUID" ] && [ "$SOURCE_GROUP" != "$GROUP" ] || fail 'source/target identity collision'
vi=0 source_systemn=0 source_datan=0 source_prebootn=0 source_recoveryn=0
while dev=$(p "$APFS" "Containers.$SOURCEIDX.Volumes.$vi.DeviceIdentifier"); do
    role=$(p "$APFS" "Containers.$SOURCEIDX.Volumes.$vi.Roles.0" || true)
    actualuuid=$(u "$(p "$APFS" "Containers.$SOURCEIDX.Volumes.$vi.APFSVolumeUUID")")
    case "$actualuuid" in
        "$SOURCE_SYSTEM_UUID") [ "$role" = System ] || fail 'source System role changed'; info "$dev"; [ "$(u "$(p "$TMP/info.plist" APFSVolumeGroupID)")" = "$SOURCE_GROUP" ] || fail 'source System group changed'; source_systemn=$((source_systemn + 1)) ;;
        "$SOURCE_DATA_UUID") [ "$role" = Data ] || fail 'source Data role changed'; info "$dev"; [ "$(u "$(p "$TMP/info.plist" APFSVolumeGroupID)")" = "$SOURCE_GROUP" ] || fail 'source Data group changed'; source_datan=$((source_datan + 1)) ;;
        "$SOURCE_PREBOOT_UUID") [ "$role" = Preboot ] || fail 'source Preboot role changed'; source_prebootn=$((source_prebootn + 1)) ;;
        "$SOURCE_RECOVERY_UUID") [ "$role" = Recovery ] || fail 'source Recovery role changed'; source_recoveryn=$((source_recoveryn + 1)) ;;
    esac
    vi=$((vi + 1))
done
[ "$source_systemn" = 1 ] && [ "$source_datan" = 1 ] && [ "$source_prebootn" = 1 ] && [ "$source_recoveryn" = 1 ] || fail 'retained source role/group UUIDs changed'
# Check role set, bound UUIDs, mounts and the actual diskutil System/Data group.
vi=0 systemn=0 datan=0 prebootn=0 recoveryn=0
while dev=$(p "$APFS" "Containers.$CTIDX.Volumes.$vi.DeviceIdentifier"); do
    role=$(p "$APFS" "Containers.$CTIDX.Volumes.$vi.Roles.0") || fail 'volume without role'
    ! p "$APFS" "Containers.$CTIDX.Volumes.$vi.Roles.1" >/dev/null || fail 'volume with multiple roles'
    info "$dev"
    [ "$(p "$TMP/info.plist" APFSContainerReference)" = "$CT" ] || fail 'volume resolves outside selected container'
    mnt=$(p "$TMP/info.plist" MountPoint) || fail "volume not mounted: $dev"
    case "$mnt" in
        ''|/) fail 'target mount is missing' ;;
        /System/Volumes/Data/private/tmp/*)
            # The paired recoveryOS mounts the Recovery volume it booted from under its RAM-disk tmp.
            [ "$role" = Recovery ] || fail "only the paired Recovery volume may be mounted under recoveryOS tmp: $mnt" ;;
        /System/Volumes/*) fail 'target mount is under running system volumes' ;;
    esac
    [ -d "$mnt" ] && [ ! -L "$mnt" ] && [ "$(cd "$mnt" && pwd -P)" = "$mnt" ] || fail 'ambiguous mount'
    actualuuid=$(u "$(p "$TMP/info.plist" VolumeUUID)")
    case "$role" in
        System) systemn=$((systemn + 1)); [ "$actualuuid" = "$SYSTEM_UUID" ] && [ "$mnt" = "$SYS" ] && [ "$(u "$(p "$TMP/info.plist" APFSVolumeGroupID)")" = "$GROUP" ] || fail 'real System UUID/group/mount differs' ;;
        Data) datan=$((datan + 1)); [ "$actualuuid" = "$DATA_UUID" ] && [ "$(u "$(p "$TMP/info.plist" APFSVolumeGroupID)")" = "$GROUP" ] || fail 'real Data UUID/group differs'; DATA="$mnt" ;;
        Preboot) prebootn=$((prebootn + 1)); [ "$actualuuid" = "$PREBOOT_UUID" ] || fail 'Preboot UUID differs'; PREBOOT="$mnt" ;;
        Recovery) recoveryn=$((recoveryn + 1)); [ "$actualuuid" = "$RECOVERY_UUID" ] || fail 'Recovery UUID differs'; RECOVERY="$mnt" ;;
        *) fail 'unexpected stub role' ;;
    esac
    info "$mnt"
    [ "$(p "$TMP/info.plist" DeviceIdentifier)" = "$dev" ] && [ "$(p "$TMP/info.plist" MountPoint)" = "$mnt" ] || fail 'mount resolves to different volume'
    vi=$((vi + 1))
done
[ "$vi" = 4 ] && [ "$systemn" = 1 ] && [ "$datan" = 1 ] && [ "$prebootn" = 1 ] && [ "$recoveryn" = 1 ] || fail 'requires exactly System/Data/Preboot/Recovery'
[ "$SYS" != "$DATA" ] && [ "$SYS" != "$PREBOOT" ] && [ "$SYS" != "$RECOVERY" ] && [ "$DATA" != "$PREBOOT" ] && [ "$DATA" != "$RECOVERY" ] && [ "$PREBOOT" != "$RECOVERY" ] || fail 'duplicate target mount points'
for a in "$SYS" "$DATA" "$PREBOOT" "$RECOVERY"; do
    for b in "$SYS" "$DATA" "$PREBOOT" "$RECOVERY"; do
        [ "$a" = "$b" ] || case "$a/" in "$b/"*) fail 'overlapping target mounts' ;; esac
    done
done
info /
[ "$(p "$TMP/info.plist" DeviceIdentifier)" != "$SYSDEV" ] && [ "$(p "$TMP/info.plist" APFSContainerReference || true)" != "$CT" ] && [ "$(u "$(p "$TMP/info.plist" APFSVolumeGroupID || true)")" != "$GROUP" ] || fail 'running root/group is target'
ci=0 grouphits=0
while p "$APFS" "Containers.$ci.ContainerReference" >/dev/null; do
    vi=0
    while dev=$(p "$APFS" "Containers.$ci.Volumes.$vi.DeviceIdentifier"); do
        role=$(p "$APFS" "Containers.$ci.Volumes.$vi.Roles.0" || true)
        case "$role" in
            System|Data) info "$dev"; actualgroup=$(u "$(p "$TMP/info.plist" APFSVolumeGroupID || true)"); if [ "$actualgroup" = "$GROUP" ]; then [ "$ci" = "$CTIDX" ] || fail 'duplicate group outside stub'; grouphits=$((grouphits + 1)); fi ;;
        esac
        vi=$((vi + 1))
    done
    ci=$((ci + 1))
done
[ "$grouphits" = 2 ] || fail 'stub group not uniquely the selected System/Data pair'
# All originally copied paired assets, not only three anchor files, are rehashed.
i=0
while role=$(p "$RES/assets.plist" "$i.role"); do
    relative=$(p "$RES/assets.plist" "$i.path") || fail 'asset path missing'
    if link=$(p "$RES/assets.plist" "$i.link"); then
        case "$role" in Preboot) root="$PREBOOT" ;; Recovery) root="$RECOVERY" ;; *) fail 'symlink asset outside Preboot/Recovery' ;; esac
        safe_link "$root" "$relative" "$link"
        i=$((i + 1))
        continue
    fi
    expected=$(p "$RES/assets.plist" "$i.md5") || fail 'asset MD5 missing'
    case "$role" in System) root="$SYS" ;; Data) root="$DATA" ;; Preboot) root="$PREBOOT" ;; Recovery) root="$RECOVERY" ;; *) fail 'unknown asset role' ;; esac
    case "$relative" in ''|/*|../*|*/../*|*/..) fail 'unsafe paired asset path' ;; esac
    # bless --setBoot during arming repoints boot/active to a new SHA-384 boot set; validate, do not hash.
    if [ "$role/$relative" = "Recovery/$GROUP/boot/active" ]; then
        safe_file "$root" "$relative"
        active=''
        IFS= read -r active < "$root/$relative" || true
        [[ "$active" =~ ^[0-9A-F]{96}$ ]] && [ -d "$root/$GROUP/boot/$active" ] && [ ! -L "$root/$GROUP/boot/$active" ] \
            || fail "Recovery boot/active does not name an existing boot set"
        i=$((i + 1))
        continue
    fi
    # Arming only renames this original copied file; its bytes remain receipt-bound.
    if [ "$role/$relative" = System/System/Library/CoreServices/SystemVersion.plist ]; then
        relative=System/Library/CoreServices/SystemVersion-disabled.plist
    fi
    safe_file "$root" "$relative"
    md5_ok "$root/$relative" "$expected" || fail "paired asset differs: $role/$relative"
    i=$((i + 1))
done
[ "$i" -ge 3 ] || fail 'paired asset receipt incomplete'
md5_ok "$PREBOOT/$GROUP/restore/BuildManifest.plist" "$(p "$TARGET" MANIFEST_MD5)" || fail 'paired manifest differs'
md5_ok "$PREBOOT/$GROUP/var/db/AdminUserRecoveryInfo.plist" "$(p "$TARGET" ADMIN_MD5)" || fail 'paired admin assets differ'
md5_ok "$RECOVERY/$GROUP/usr/standalone/firmware/arm64eBaseSystem.dmg" "$(p "$TARGET" BASESYSTEM_MD5)" || fail 'paired BaseSystem differs'
# Find real ESP by GPT DiskUUID rather than FAT UUID; never mount it here.
/usr/sbin/diskutil list -plist > "$TMP/disks.plist" || fail 'physical partition inventory failed'
di=0 esphits=0 stubhits=0
while p "$TMP/disks.plist" "AllDisksAndPartitions.$di.DeviceIdentifier" >/dev/null; do
    pi=0
    while content=$(p "$TMP/disks.plist" "AllDisksAndPartitions.$di.Partitions.$pi.Content"); do
        dev=$(p "$TMP/disks.plist" "AllDisksAndPartitions.$di.Partitions.$pi.DeviceIdentifier" || true)
        if [ -z "$dev" ]; then pi=$((pi + 1)); continue; fi
        guid=$(p "$TMP/disks.plist" "AllDisksAndPartitions.$di.Partitions.$pi.DiskUUID" || true)
        if [ "$(u "$guid")" = "$STUB_PARTUUID" ]; then
            stubhits=$((stubhits + 1))
            [ "$dev" = "$STORE" ] || fail 'stub physical GUID occurs on another partition'
        fi
        if [ "$(u "$guid")" = "$ESP_PARTUUID" ]; then
            esphits=$((esphits + 1)); info "$dev"
            [ "$(u "$(p "$TMP/info.plist" DiskUUID)")" = "$ESP_PARTUUID" ] && [ "$(p "$TMP/info.plist" Content)" = EFI ] && [ "$(p "$TMP/info.plist" ParentWholeDisk)" = "$WHOLE" ] || fail 'ESP type/GUID/disk differs'
            fs=$(p "$TMP/info.plist" FilesystemType)
            [ "$fs" = msdos ] || fail 'ESP is not FAT'
            ESP=$(p "$TMP/info.plist" MountPoint) || fail 'ESP must already be mounted'
            [ -d "$ESP" ] && [ ! -L "$ESP" ] && [ "$(cd "$ESP" && pwd -P)" = "$ESP" ] || fail 'ambiguous ESP mount'
            info "$ESP"
            [ "$(p "$TMP/info.plist" DeviceIdentifier)" = "$dev" ] || fail 'ESP mount resolves to different device'
        fi
        pi=$((pi + 1))
    done
    di=$((di + 1))
done
[ "$esphits" = 1 ] || fail 'ESP GUID missing/duplicated'
[ "$stubhits" = 1 ] || fail 'stub physical GUID missing/duplicated'
# Match the complete recorded post-prep partition baseline, not only the stub and ESP.
info "$WHOLE"
/usr/sbin/diskutil list -plist "$WHOLE" > "$TMP/whole.plist" || fail 'target physical layout query failed'
expected_count=0
while expected_uuid=$(p "$TARGET" "PARTITIONS.$expected_count.uuid"); do
    uuid_ok "$expected_uuid" || fail 'malformed recorded baseline GUID'
    expected_content=$(p "$TARGET" "PARTITIONS.$expected_count.content") || fail 'baseline type missing'
    expected_size=$(p "$TARGET" "PARTITIONS.$expected_count.size") || fail 'baseline size missing'
    pi=0 found=0
    while content=$(p "$TMP/whole.plist" "AllDisksAndPartitions.0.Partitions.$pi.Content"); do
        actual_uuid=$(u "$(p "$TMP/whole.plist" "AllDisksAndPartitions.0.Partitions.$pi.DiskUUID" || true)")
        if [ "$actual_uuid" = "$expected_uuid" ]; then
            found=$((found + 1))
            [ "$content" = "$expected_content" ] && [ "$(p "$TMP/whole.plist" "AllDisksAndPartitions.0.Partitions.$pi.Size")" = "$expected_size" ] || fail 'recorded partition type/size changed'
        fi
        pi=$((pi + 1))
    done
    [ "$found" = 1 ] || fail 'recorded partition GUID absent/duplicated'
    expected_count=$((expected_count + 1))
done
[ "$expected_count" = 6 ] || fail 'expected one fresh Apple baseline plus stub, ESP and Linux'
pi=0 actual_count=0
while content=$(p "$TMP/whole.plist" "AllDisksAndPartitions.0.Partitions.$pi.Content"); do
    if p "$TMP/whole.plist" "AllDisksAndPartitions.0.Partitions.$pi.DeviceIdentifier" >/dev/null; then actual_count=$((actual_count + 1)); fi
    pi=$((pi + 1))
done
[ "$actual_count" = "$expected_count" ] || fail 'unexpected additional physical partition'
safe_file "$ESP" "$STAGE2_PATH"
md5_ok "$ESP/$STAGE2_PATH" "$(p "$TARGET" STAGE2_MD5)" || fail 'real ESP Stage 2 differs'
payload_index=0
while relative=$(p "$TARGET" "PAYLOAD_FILES.$payload_index.path"); do
    safe_file "$ESP" "$relative"
    md5_ok "$ESP/$relative" "$(p "$TARGET" "PAYLOAD_FILES.$payload_index.md5")" || fail 'ESP kernel/DTB/initramfs differs'
    payload_index=$((payload_index + 1))
done
[ "$payload_index" = 3 ] || fail 'requires exactly three boot= dependencies'
# This is current Recovery's chosen firmware, tied to the actual group by bputil.
/usr/sbin/ioreg -a -p IODeviceTree -n chosen -r -d 1 > "$TMP/chosen.plist" || fail 'chosen firmware query failed'
! p "$TMP/chosen.plist" 1.IORegistryEntryName >/dev/null || fail 'multiple chosen firmware entries'
# recoveryOS has no base64. plutil prints data as base64; "mBoot-20457.60.65.0.1" is 21 bytes (a multiple
# of 3), so its encoding is an exact prefix and the required NUL padding can only encode as A/= characters.
for key in system-firmware-version firmware-version; do
    value=$(p "$TMP/chosen.plist" "0.$key") || fail "cannot read chosen $key"
    [[ "$value" =~ ^bUJvb3QtMjA0NTcuNjAuNjUuMC4xA+=*$ ]] || fail "paired Recovery chosen firmware mismatch: $key=$value"
done
/usr/bin/pmset -g batt > "$TMP/power.txt" || fail 'cannot query AC power'
[ -n "$(/usr/bin/sed -n "/Now drawing from 'AC Power'/p" "$TMP/power.txt")" ] || fail 'AC power required'
/usr/bin/bputil -d -v "$GROUP" > "$TMP/bputil.txt" 2>&1 || fail 'cannot query actual group Recovery pairing'
[ -n "$(/usr/bin/sed -n '/one true recoveryOS/p' "$TMP/bputil.txt")" ] && [ -n "$(/usr/bin/sed -n '/: Paired/p' "$TMP/bputil.txt")" ] || fail 'not actual stub paired 1TR'
[ -n "$(/usr/bin/sed -n '/Kernel CTRR Status: *Disabled/p' "$TMP/bputil.txt")" ] \
    || fail "raw enrollment requires owner action first: bputil -nc -v $GROUP"
[ "$(p "$RES/stage1-receipt.json" enrollment_format)" = raw ] \
    && [ "$(p "$RES/stage1-receipt.json" entry_point)" = 2048 ] \
    && [ "$(p "$RES/stage1-receipt.json" lowest_virtual_address)" = 0 ] \
    || fail 'Stage 1 receipt is not canonical raw enrollment'
[ -f "$SYS/.IAPhysicalMedia" ] && [ ! -L "$SYS/.IAPhysicalMedia" ] && [ -f "$SYS/System/Library/CoreServices/SystemVersion-disabled.plist" ] && [ ! -e "$SYS/System/Library/CoreServices/SystemVersion.plist" ] || fail 'install-media state not intact'
[ "$(p "$SYS/.IAPhysicalMedia" AppName)" = 'Finish Installation.app' ] && [ "$(p "$SYS/.IAPhysicalMedia" ProductBuildVersion)" = 26C5035r ] && [ "$(p "$SYS/System/Library/CoreServices/SystemVersion-disabled.plist" ProductBuildVersion)" = 26C5035r ] || fail 'install-media/SystemVersion build binding differs'
# Upstream --group alone is insufficient: actual group was independently checked above.
BOOT_MD5=$(p "$TARGET" BOOT_MD5)
/bin/bash "$RES/enroll-stage1.sh" --check --raw --no-rollback --group "$GROUP" --log "$TMP/enrollment-check.log" "$SYS" "$RES/boot.bin" "$BOOT_MD5" || fail 'pinned upstream Recovery checks failed'
if [ "$MODE" = check ]; then
    printf '%s\n' "Read-only Recovery checks complete; target untouched. Diagnostics: $TMP" 'Enrollment remains separately authorized: bash step2.sh --apply'
    exit 0
fi
# Upstream repeats its Recovery and object checks, then prompts before configure-boot.
/bin/bash "$RES/enroll-stage1.sh" --apply --raw --no-rollback --group "$GROUP" --log "$TMP/enrollment-apply.log" "$SYS" "$RES/boot.bin" "$BOOT_MD5" || fail 'enrollment/authentication/confirmation failed'
# Only successful enrollment reaches target finalization. No policy cleanup on failures.
/sbin/mount -u -w "$SYS" || fail 'enrolled but remount failed; media state retained'
CS="$SYS/System/Library/CoreServices"
[ ! -e "$CS/SystemVersion.plist" ] && [ -f "$CS/SystemVersion-disabled.plist" ] && [ -f "$SYS/.IAPhysicalMedia" ] || fail 'enrolled but finalization state changed; retain media marker'
# Restore visible OS first; failure leaves the media marker in place.
/bin/mv "$CS/SystemVersion-disabled.plist" "$CS/SystemVersion.plist" || fail 'enrolled but cannot restore SystemVersion; media marker retained'
/bin/mv -f "$SYS/.IAPhysicalMedia" "$SYS/IAPhysicalMedia-disabled.plist" || fail 'enrolled but cannot retire media marker'
/bin/sync
printf '%s\n' 'Enrollment succeeded and install-media path retired. Hardware boot remains unproven.' "No reboot performed. Diagnostics: $TMP"
