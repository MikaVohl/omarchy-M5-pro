#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
#
# Enroll a Stage 1 boot object for one macOS volume, from that volume's paired
# One True recoveryOS (1TR), using the paired target's raw enrollment only.
#
# recoveryOS has few tools: no grep, cat, dirname or shasum, and bash 3.2. This
# script uses bash builtins and md5, sed, tee, date, sync, script, diskutil,
# bputil and kmutil. Every check runs before anything changes.

usage() {
    echo "usage: bash enroll-stage1.sh [options] VOLUME OBJECT MD5"
    echo
    echo "  VOLUME           the target System volume: a name under /Volumes, or a path"
    echo "  OBJECT           the receipt-bound raw Stage 1 image"
    echo "  MD5              OBJECT's expected MD5, as printed by 'md5 -q' or 'md5sum'"
    echo
    echo "  --raw            OBJECT is a raw image: enroll it with"
    echo "                   --raw --entry-point 2048 --lowest-virtual-address 0"
    echo "  --keep FILE MD5  a rollback image that must be present with this MD5 (repeatable)"
    echo "  --no-rollback    enroll without any --keep image"
    echo "  --group UUID     required, independently verified APFS System/Data volume group"
    echo "  --log FILE       append the session here (default: a per-process log under TMPDIR)"
    echo "  --check          default; run every check, then stop without enrolling"
    echo "  --apply          explicitly enroll after physical backup confirmation and owner authentication"
}

RAW=0 CHECK_ONLY=1 NO_ROLLBACK=0 GROUP="" LOG="" MODE_SEEN=0
KEEP_FILES=() KEEP_MD5S=()
while [ $# -gt 0 ]; do
    case "$1" in
        --raw) RAW=1 ;;
        --check) [ $MODE_SEEN -eq 0 ] || { usage; exit 2; }; CHECK_ONLY=1; MODE_SEEN=1 ;;
        --apply) [ $MODE_SEEN -eq 0 ] || { usage; exit 2; }; CHECK_ONLY=0; MODE_SEEN=1 ;;
        --no-rollback) NO_ROLLBACK=1 ;;
        --group|--log)
            [ $# -ge 2 ] || { usage; exit 2; }
            if [ "$1" = --group ]; then GROUP="$2"; else LOG="$2"; fi
            shift ;;
        --keep)
            [ $# -ge 3 ] || { usage; exit 2; }
            KEEP_FILES+=("$2"); KEEP_MD5S+=("$3")
            shift 2 ;;
        -h|--help) usage; exit 0 ;;
        --) shift; break ;;
        -*) echo "unknown option: $1"; usage; exit 2 ;;
        *) break ;;
    esac
    shift
done
if [ $# -ne 3 ]; then
    usage
    exit 2
fi

abs() {
    case "$1" in
        /*) printf '%s' "$1" ;;
        *) printf '%s/%s' "$PWD" "$1" ;;
    esac
}

case "$1" in
    /*) SYS="$1" ;;
    *) SYS="/Volumes/$1" ;;
esac
OBJ=$(abs "$2")
OBJ_MD5="$3"
[ -n "$LOG" ] || LOG="${TMPDIR:-/tmp}/enroll-stage1.$$.log"
BP="${TMPDIR:-/tmp}/enroll-stage1-bputil.txt"

if ! { echo; echo "=== enroll-stage1.sh $(date)"; } >> "$LOG"; then
    echo "Cannot write the log $LOG. Nothing was changed."
    exit 2
fi
echo "args: raw=$RAW compress=$COMPRESS check=$CHECK_ONLY volume=$SYS object=$OBJ" >> "$LOG"

say() { echo "$*" | tee -a "$LOG"; }
fail() {
    say ""
    say "STOPPED: $*"
    say "Enrollment stopped. Review the command status; no automatic retry or reboot. Log: $LOG."
    sync
    exit 1
}
# Matches a fixed text in a file, without grep.
has() { [ -n "$(sed -n "/$1/p" "$2")" ]; }
md5_ok() { [ -f "$1" ] && [ "$(md5 -q "$1")" = "$2" ]; }

say "== Checks (nothing changes yet)"

if [ ! -d "$SYS" ]; then
    say "Mounted volumes:"
    for v in /Volumes/*; do say "  $v"; done
    fail "$SYS is not mounted. Mount it in Disk Utility, then run this again."
fi
say "  ok  $SYS is mounted"

ACTUAL_GROUP=$(diskutil info "$SYS" | sed -n 's/^ *APFS Volume Group: *//p')
[ -n "$GROUP" ] && [ "$GROUP" = "$ACTUAL_GROUP" ] || fail 'explicit group does not match the mounted System volume'
[[ "$GROUP" =~ ^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$ ]] ||
    fail "no APFS volume group UUID for $SYS (got '$GROUP'). Pass it with --group."
say "  ok  volume group $GROUP"

[[ "$OBJ_MD5" =~ ^[0-9a-f]{32}$ ]] || fail "'$OBJ_MD5' is not a lowercase MD5"
md5_ok "$OBJ" "$OBJ_MD5" || fail "$OBJ is missing or its MD5 is not $OBJ_MD5"
say "  ok  $OBJ, MD5 $OBJ_MD5"

i=0
while [ $i -lt ${#KEEP_FILES[@]} ]; do
    k=$(abs "${KEEP_FILES[$i]}")
    md5_ok "$k" "${KEEP_MD5S[$i]}" || fail "rollback image $k is missing or its MD5 is not ${KEEP_MD5S[$i]}"
    say "  ok  rollback image $k"
    i=$((i + 1))
done
if [ $i -eq 0 ] && [ $NO_ROLLBACK -eq 0 ]; then
    fail "no rollback image. Name the Stage 1 to go back to with --keep FILE MD5, or pass --no-rollback."
fi

bputil -d -v "$GROUP" > "$BP" 2>&1
tee -a "$LOG" < "$BP" > /dev/null
has 'one true recoveryOS' "$BP" ||
    fail "this is not 1TR. Shut down, hold the power button until 'Loading startup options', and choose Options."
has ': Paired' "$BP" ||
    fail "this recoveryOS is not paired with $SYS. Make it the startup disk, then enter 1TR again."
say "  ok  1TR, paired with $SYS"

[ $RAW -eq 1 ] || fail 'only --raw Stage 1 enrollment is supported'
has 'Kernel CTRR Status: *Disabled' "$BP" ||
    fail "Kernel CTRR must be explicitly disabled by the owner for this stub: bputil -nc -v $GROUP"
say "  ok  Kernel CTRR is disabled"
set -- kmutil configure-boot -c "$OBJ" --raw --entry-point 2048 --lowest-virtual-address 0 -v "$SYS"

say ""
say "Command: $*"
if [ $CHECK_ONLY -eq 1 ]; then
    say "All checks passed (--check). Nothing was changed."
    sync
    exit 0
fi

[ -t 0 ] || fail '--apply requires the physical owner in an interactive paired-1TR Terminal'
read -r -p "Confirm a current independently recoverable backup. Type BACKUP VERIFIED: " backup_answer
[ "$backup_answer" = 'BACKUP VERIFIED' ] || fail 'fresh backup confirmation required; no waiver supported'
[ "$(id -u)" = 0 ] || fail '--apply requires root in paired 1TR'
read -r -p "Type yes to enroll this Stage 1 for $SYS: " answer
echo "answer: $answer" >> "$LOG"
[ "$answer" = yes ] || fail "not confirmed"

say ""
say "== Enrolling. kmutil asks for the volume owner's password, and may ask which installation: pick $SYS."
if command -v script > /dev/null 2>&1; then
    script -q -a "$LOG" "$@"
    rc=$?
else
    "$@"
    rc=$?
fi
say "kmutil exit status: $rc"
sync
if [ $rc -eq 0 ]; then
    say "DONE. Raw Stage 1 enrollment succeeded. No reboot was performed; boot qualification is separate."
else
    say "kmutil FAILED. It normally leaves the previous Stage 1 enrolled. The log is $LOG."
    exit 1
fi
