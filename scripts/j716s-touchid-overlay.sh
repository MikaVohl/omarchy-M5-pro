#!/bin/bash -p
# J716s Touch ID overlay for omarchy-mac: admits the J716s board in its Touch ID
# eligibility, reads the calibration as the sensor node's first firmware-name,
# and pins the J716s driver policy (trusted keys and FileVault API off, xART
# guard 1). omarchy-mac's own enable step adds xart_writes=1 provision_keybag=1
# once the owner agrees, exactly as on M2/M3.
#
#   install   copy the overlay to /usr/local/lib/j716s, patch omarchy-mac, add
#             the modprobe options and a pacman hook that re-applies the patch
#   reapply   (pacman hook) patch omarchy-mac again after it is reinstalled
#   remove    restore omarchy-mac's files and remove the overlay
#
# A patch that no longer applies leaves omarchy-mac untouched: Touch ID then
# reports unsupported:board, which is safe. Nothing here loads the driver.
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/bin

home=/usr/local/lib/j716s/omarchy-mac-touchid
patch_name=omarchy-mac-j716s-touchid.patch
options=/etc/modprobe.d/j716s-touchid.conf
hook=/etc/pacman.d/hooks/89-j716s-omarchy-mac-touchid.hook
# Sorts before omarchy-mac's 90-omarchy-mac-touchid.hook, so its post-update
# status check already sees the patched eligibility.

fail() { echo "j716s-touchid-overlay: $1" >&2; exit 1; }
(( EUID == 0 )) || fail "run as root"
tr '\0' '\n' </proc/device-tree/compatible | grep -Fxq apple,j716s || fail "not a J716s"

patch_omarchy_mac() {
  local source=$1
  if patch --dry-run --batch --silent --reverse --force -p1 -d / <"$source" >/dev/null 2>&1; then
    echo "omarchy-mac already carries the J716s Touch ID overlay."
  elif patch --dry-run --batch --silent --fuzz=0 -p1 -d / <"$source" >/dev/null 2>&1; then
    patch --batch --silent --fuzz=0 --no-backup-if-mismatch -p1 -d / <"$source"
    echo "Patched omarchy-mac for J716s Touch ID."
  else
    echo "omarchy-mac changed and the J716s overlay no longer applies; Touch ID stays unsupported here." >&2
    return 1
  fi
}

case ${1:-} in
  install)
    project=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
    pacman -Q omarchy-mac >/dev/null || fail "omarchy-mac is not installed"
    install -d -m 0755 "$home"
    install -m 0644 "$project/patches/$patch_name" "$home/$patch_name"
    install -m 0755 "$project/scripts/j716s-touchid-overlay.sh" "$home/j716s-touchid-overlay"
    patch_omarchy_mac "$home/$patch_name"
    printf '%s\n' "# J716s Touch ID policy (omarchy-M5-pro); the profile refuses other values." \
      "options apple_sep trusted_keys=0 filevault_api=0 xart_guard=1" >"$options.new"
    chmod 0644 "$options.new" && mv -fT "$options.new" "$options"
    install -d -m 0755 "${hook%/*}"
    cat >"$hook.new" <<EOF
# Re-apply the J716s Touch ID overlay (omarchy-M5-pro) when omarchy-mac is reinstalled.
[Trigger]
Type = Path
Operation = Install
Operation = Upgrade
Target = usr/lib/omarchy-mac/touchid-eligibility
Target = usr/lib/omarchy-mac/touchid-enable

[Action]
Description = Applying the J716s Touch ID overlay to omarchy-mac...
When = PostTransaction
Exec = $home/j716s-touchid-overlay reapply
EOF
    chmod 0644 "$hook.new" && mv -fT "$hook.new" "$hook"
    echo "Installed $options and $hook."
    ;;
  reapply)
    # Never fail the pacman transaction.
    patch_omarchy_mac "$home/$patch_name" || true
    ;;
  remove)
    if [[ -r $home/$patch_name ]] &&
      patch --dry-run --batch --silent --reverse --force -p1 -d / <"$home/$patch_name" >/dev/null 2>&1; then
      patch --batch --silent --reverse --force --no-backup-if-mismatch -p1 -d / <"$home/$patch_name"
    fi
    rm -f "$options" "$hook"
    rm -rf "$home"
    echo "Removed the J716s Touch ID overlay; omarchy-mac's own files are restored."
    ;;
  *)
    echo "Usage: $0 {install|reapply|remove}" >&2
    exit 2
    ;;
esac
