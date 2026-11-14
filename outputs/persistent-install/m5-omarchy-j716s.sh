#!/bin/bash
# Warren's official EDGE phase composition, adapted to the UUID-bound J716s
# persistent ext4 image. No firmware, disk, loader or kernel deployment occurs.
# check (default): read-only actual-machine survey, never escalates privileges.
# image: root in marked native ARM64 guest/chroot, downloads signed packages.
# provision --user USER: root on actual J716s, offline fresh-owner setup.
set -euo pipefail
export LC_ALL=C.UTF-8
export PATH=/usr/local/sbin:/usr/local/bin:/usr/bin
readonly OMARCHY_FPR=40DFB630FF42BCFFB047046CF0134EE680CAC571
readonly ASAHI_FPR=12CE6799A94A3F1B5DDFFE88F576553597FB8FEB
readonly STATE=/var/lib/j716s
readonly RUNTIME=/usr/share/omarchy
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
readonly policy=$here/policies/j716s-policy.py
readonly keys=$here/keys
say() { printf 'J716s Omarchy: %s\n' "$*"; }
die() { say "$*" >&2; exit 1; }
require_root() { (( EUID == 0 )) || die 'Run this explicit operation as root; no automatic sudo escalation.'; }

check_keys() {
  python - "$keys" "$OMARCHY_FPR" "$ASAHI_FPR" <<'PY'
import hashlib,json,pathlib,subprocess,sys,tempfile
root=pathlib.Path(sys.argv[1]); receipts=json.loads((root/'sources.json').read_text())['keys']
with tempfile.TemporaryDirectory(prefix='j716s-keycheck.') as home:
 try:
  for name,want in [('omarchy-pkgs.gpg',sys.argv[2]),('asahi-alarm.gpg',sys.argv[3])]:
   path=root/name
   if not path.is_file(): raise SystemExit('Missing public signing key: '+str(path))
   text=subprocess.check_output(['gpg','--batch','--no-options','--homedir',home,'--show-keys','--with-colons',str(path)],text=True)
   primary=[];next_primary=False
   for line in text.splitlines():
    fields=line.split(':')
    if fields[0]=='pub': next_primary=True
    elif fields[0]=='sub': next_primary=False
    elif fields[0]=='fpr' and next_primary: primary.append(fields[9]);next_primary=False
   if primary != [want]: raise SystemExit('Wrong primary signing key: '+name+' '+str(primary))
   receipt=next(r for r in receipts if r['file']==name)
   if receipt['primary_fingerprint']!=want or hashlib.sha256(path.read_bytes()).hexdigest()!=receipt['sha256']:
    raise SystemExit('Public key provenance receipt mismatch: '+name)
 finally:
  subprocess.run(['gpgconf','--homedir',home,'--kill','all'],check=True)
PY
}

mask_hazards() {
  local unit
  install -d -m 0755 /etc/systemd/system
  for unit in first-boot.service linux-modules-cleanup.service speakersafetyd.service omarchy-provision-owner.service omarchy-provision-hardware.service; do
    ln -sfn /dev/null "/etc/systemd/system/$unit"
  done
}

secure_login() {
  # No temporary or persistent passwordless sudo grants survive this image.
  python - <<'PY'
from pathlib import Path
for path in [Path('/etc/sudoers'),*Path('/etc/sudoers.d').glob('*')]:
 if not path.is_file(): continue
 lines=path.read_text().splitlines(keepends=True)
 if any(('NOPASSWD' in line or '!authenticate' in line) and not line.lstrip().startswith('#') for line in lines):
  path.write_text(''.join('# J716s password-required policy: '+line if ('NOPASSWD' in line or '!authenticate' in line) and not line.lstrip().startswith('#') else line for line in lines))
Path('/etc/sudoers.d/90-j716s-wheel').write_text('%wheel ALL=(ALL:ALL) ALL\n')
Path('/etc/sudoers.d/90-j716s-wheel').chmod(0o440)
# Stock SDDM assets remain; the owner must authenticate normally.
for path in Path('/etc/sddm.conf.d').glob('*.conf'):
 lines=path.read_text().splitlines(keepends=True);section='';out=[]
 for line in lines:
  if line.strip().startswith('['): section=line.strip()
  if section=='[Autologin]' and not line.lstrip().startswith('#') and '=' in line:
   line='# J716s disables autologin: '+line
  out.append(line)
 path.write_text(''.join(out))
PY
  visudo -cf /etc/sudoers >/dev/null
  passwd -l root >/dev/null
  /usr/bin/systemctl --root=/ disable sshd.service sshd.socket sddm.service >/dev/null 2>&1 || true
  mask_hazards
}

install_policy() {
  [[ $here == /usr/local/lib/j716s ]] || die 'Install script, policies and keys together under /usr/local/lib/j716s.'
  install -Dm644 "$here/policies/00-j716s-ownership-guard.hook" /etc/pacman.d/hooks/00-j716s-ownership-guard.hook
  install -Dm644 "$here/policies/00-j716s-remove-guard.hook" /etc/pacman.d/hooks/00-j716s-remove-guard.hook
  install -Dm644 "$here/policies/90-j716s-no-speakers.conf" /etc/wireplumber/wireplumber.conf.d/90-j716s-no-speakers.conf
  # App-scoped VA-API selection: Aurora DRM is not named asahi, so autodetection
  # cannot select the separately packaged V4L2-request decoder backend.
  install -Dm644 "$here/policies/com.moonlight_stream.Moonlight.desktop" /usr/local/share/applications/com.moonlight_stream.Moonlight.desktop
  python "$policy" configure
  mask_hazards
}

import_keys() {
  [[ -r /usr/share/pacman/keyrings/archlinuxarm.gpg ]] || die 'Official ALARM keyring is missing.'
  pacman-key --init
  pacman-key --populate archlinuxarm
  # Populate Arch's keyring only if the official base actually supplies it.
  [[ ! -r /usr/share/pacman/keyrings/archlinux.gpg ]] || pacman-key --populate archlinux
  pacman-key --add "$keys/omarchy-pkgs.gpg" "$keys/asahi-alarm.gpg"
  pacman-key --lsign-key "$OMARCHY_FPR" "$ASAHI_FPR"
}

bootstrap_templates() {
  python - "$here/policies" <<'PY'
import hashlib,json,pathlib,shutil,sys
root=pathlib.Path(sys.argv[1]);receipt=json.loads((root/'sources.json').read_text())
for item in receipt['templates']:
 if hashlib.sha256((root/item['file']).read_bytes()).hexdigest()!=item['sha256']:
  raise SystemExit('Official bootstrap template receipt mismatch: '+item['file'])
shutil.copyfile(root/'pacman-edge.conf','/etc/pacman.conf')
shutil.copyfile(root/'mirrorlist-edge','/etc/pacman.d/mirrorlist')
PY
  install_policy
}

signed_install() {
  local label=$1 filename
  shift
  local -a archives=()
  # Resolve every requested package. Never silently intersect the manifest with
  # repository names, and never fall back to unsigned/AUR source builds.
  pacman -Sp --needed --noconfirm --ask 4 --print-format '%f' "$@" >"$STATE/$label-plan.txt"
  pacman -Sw --needed --noconfirm --ask 4 "$@"
  while IFS= read -r filename; do
    [[ -n $filename ]] || continue
    [[ $filename != */* && -f /var/cache/pacman/pkg/$filename ]] || die "Missing resolved signed package archive: $filename"
    archives+=("/var/cache/pacman/pkg/$filename")
  done <"$STATE/$label-plan.txt"
  if ((${#archives[@]})); then
    # Masks dangerous incoming hooks by exact basename before ALPM evaluates
    # them. Scriptlets and boot/module/private-Mesa ownership are fail-closed.
    python "$policy" scan-cache "${archives[@]}"
  fi
  export OMARCHY_ALLOW_DIRECT_PACMAN=1
  pacman -S --needed --noconfirm --ask 4 "$@"
  python "$policy" configure
  mask_hazards
}

image() {
  require_root
  [[ $(uname -m) == aarch64 ]] || die 'Image builds require native ARM64.'
  [[ ${J716S_IMAGE_BUILD:-} == 1 && -f /etc/j716s/image-build && ! -L /etc/j716s/image-build ]] || die 'Explicit image guest/chroot marker is missing.'
  [[ $(</etc/j716s/image-build) == j716s-arm64-image-v1 ]] || die 'Invalid image-build marker.'
  python - <<'PY'
from pathlib import Path
for path in (Path('/proc/device-tree/compatible'), Path('/sys/firmware/devicetree/base/compatible')):
    if path.is_file() and any(item.startswith(b'apple,') for item in path.read_bytes().split(b'\0')):
        raise SystemExit('Image mode refuses a physical Apple device-tree host')
PY
  [[ ! -e /etc/j716s/boot.json ]] || die 'Image mode refuses a deployed root.'
  [[ ! -e $STATE/omarchy-image.json ]] || die 'Image already provisioned; use a fresh build root.'
  [[ ! -e /var/lib/pacman/db.lck ]] || die 'Another package transaction is active.'
  install -d -m 0755 "$STATE" /etc/pacman.d /var/lib/omarchy/image
  trap 'mask_hazards; secure_login' EXIT
  check_keys
  # Official platform detection has an image manifest API: no fake Apple DT or
  # replacement detector is needed to install Apple packages in an ARM guest.
  printf 'format=1\nplatform=aarch64-apple\n' >/var/lib/omarchy/image/target
  chmod 0644 /var/lib/omarchy/image/target
  bootstrap_templates
  import_keys
  export OMARCHY_ALLOW_DIRECT_PACMAN=1
  pacman -Syy --noconfirm
  signed_install core base-devel sudo xdg-user-dirs jq omarchy-dev omarchy-settings-dev omarchy-mac
  [[ -x /usr/bin/omarchy-apply-pacman ]] || die 'Signed official runtime bootstrap did not install omarchy-apply-pacman.'
  export OMARCHY_PATH=$RUNTIME
  omarchy-apply-pacman edge
  install_policy
  python "$policy" manifest "$STATE/manifest-packages.txt"
  local -a desktop
  mapfile -t desktop <"$STATE/manifest-packages.txt"
  signed_install desktop "${desktop[@]}"
  secure_login
  python "$policy" receipt
  # The marker belongs only to the away-from-hardware package build, not to
  # stock hardware provisioning. Our firstboot performs the real phases.
  rm -f /var/lib/omarchy/image/target
  say 'Signed official EDGE runtime and full compatible desktop baked.'
}

apply_phases() {
  export OMARCHY_PATH=$RUNTIME OMARCHY_INSTALL=$RUNTIME/install
  export OMARCHY_INSTALL_USER=$owner OMARCHY_FIRST_INSTALL=1 OMARCHY_UPGRADE=0 OMARCHY_MIRROR=edge
  export OMARCHY_LOG_TO_STDOUT=1 OMARCHY_INSTALL_LOG_FILE=/var/log/omarchy-install.log
  export PATH="$RUNTIME/bin:/usr/local/sbin:/usr/local/bin:/usr/bin"
  # Keep hazardous units masked throughout. The stock enable-services phase
  # still enables every compatible service; it cannot enable SDDM prematurely
  # or temporarily unmask the custom kernel's module cleanup.
  systemctl() {
    local argument action='' remaining_units=0 blocked=0
    local -a forwarded=()
    for argument in "$@"; do
      case $argument in enable|reenable|preset|preset-all|unmask|start|restart|try-restart) action=$argument ;; esac
    done
    [[ -n $action ]] || { /usr/bin/systemctl "$@"; return; }
    [[ $action != preset-all ]] || die 'Unbounded service preset is not permitted during firstboot.'
    for argument in "$@"; do
      case $argument in
        first-boot|first-boot.service|linux-modules-cleanup|linux-modules-cleanup.service|speakersafetyd|speakersafetyd.service|sddm|sddm.service|sshd|sshd.service|sshd.socket|omarchy-provision-owner|omarchy-provision-owner.service|omarchy-provision-hardware|omarchy-provision-hardware.service)
          say "Keeping $argument disabled/masked during owner provisioning"; blocked=1 ;;
        *)
          forwarded+=("$argument")
          case $argument in -*|enable|reenable|preset|unmask|start|restart|try-restart) ;; *) remaining_units=$((remaining_units + 1)) ;; esac ;;
      esac
    done
    # A mixed request must still enable its compatible services.
    (( blocked == 0 || remaining_units > 0 )) || return 0
    /usr/bin/systemctl "${forwarded[@]}"
  }
  export -f systemctl say die
  source "$OMARCHY_INSTALL/helpers/logging.sh"
  eval "stock_$(declare -f run_logged)"
  run_logged() {
    case $1 in
      */config/snapper.sh) say 'Skipping official Snapper phase: persistent root is ext4, not Btrfs.'; return 0 ;;
      */hardware/platform-setup.sh) say 'Skipping official setup-boot only: J716s UUID-bound loader belongs to deployment receipt.'; return 0 ;;
    esac
    stock_run_logged "$@"
  }
  start_install_log
  source "$OMARCHY_INSTALL/config/all.sh"
  source "$OMARCHY_INSTALL/hardware/all.sh"
  # The skipped platform phase is boot + system. Keep its system half intact.
  [[ -x /usr/lib/omarchy/mac/setup-system ]] || die 'Required official Apple system lifecycle is missing.'
  omarchy-lifecycle-dispatch setup-system
  source "$OMARCHY_INSTALL/login/all.sh"
  source "$OMARCHY_INSTALL/post-install/all.sh"
  stop_install_log
  unset -f systemctl
  python "$policy" configure
}

provision_inner() {
  require_root
  [[ ${J716S_OFFLINE_NAMESPACE:-} == 1 ]] || die 'Internal provision operation requires private namespaces.'
  owner=$1
  python "$policy" boot
  local release home path
  release=$(</etc/j716s/kernel.release)
  for path in /boot "/usr/lib/modules/$release" /opt/mesa-j716s; do
    [[ -d $path ]] || die "Missing protected deployment tree $path"
    mount --bind "$path" "$path"
    mount -o remount,bind,ro "$path"
  done
  # /run is shared with PID1 by default: keep the offline transaction marker
  # local to this mount namespace and do not alter host service state files.
  mount --bind /run /run
  mount -t tmpfs -o mode=0755 tmpfs /run/j716s
  : >/run/j716s/offline-provision
  export J716S_OFFLINE_PROVISION=1
  trap 'mask_hazards; secure_login' EXIT
  import_keys
  python "$policy" verify-receipt
  mask_hazards
  python "$policy" fresh-owner "$owner"
  apply_phases
  secure_login
  home=$(getent passwd "$owner" | cut -d: -f6)
  runuser -u "$owner" -- env HOME="$home" USER="$owner" LOGNAME="$owner" OMARCHY_PATH="$RUNTIME" \
    OMARCHY_INSTALL="$RUNTIME/install" OMARCHY_SETUP_CONTEXT=provision-owner MISE_OFFLINE=true \
    OMARCHY_LOG_TO_STDOUT=1 OMARCHY_INSTALL_LOG_FILE=/var/log/omarchy-install.log \
    PATH="$RUNTIME/bin:/usr/local/sbin:/usr/local/bin:/usr/bin" omarchy-provision-user --first-install
  [[ -x /usr/local/libexec/j716s-session-env ]] || die 'Required private Mesa session integration is missing.'
  /usr/local/libexec/j716s-session-env --user "$owner"
  # The stock runtime invokes omarchy-provision-first-run in the authenticated
  # graphical session; do not synthesize its completion or run it on tty1.
  [[ -x /usr/bin/omarchy-provision-first-run ]] || die 'Stock first-run command is missing.'
  python "$policy" boot
  python "$policy" verify-receipt
  python - "$owner" <<'PY'
import json,pathlib,sys
path=pathlib.Path('/var/lib/j716s/owner-provisioning.json');value=json.loads(path.read_text())
if value['user']!=sys.argv[1]: raise SystemExit('Owner changed during provisioning')
value['status']='complete';value['first_run']='stock authenticated graphical session'
path.write_text(json.dumps(value,indent=2)+'\n')
PY
  say 'Official system/user phases complete offline; caller may enable SDDM password login.'
}

provision() {
  require_root
  [[ $# == 2 && $1 == --user && $2 =~ ^[a-z_][a-z0-9_-]*$ && $2 != root ]] || die 'Usage: provision --user USER'
  [[ ! -e /etc/j716s/image-build ]] || die 'Remove the build marker before deployment.'
  [[ -f $STATE/omarchy-image.json ]] || die 'Required baked-package receipt is missing.'
  check_keys
  python "$policy" boot
  # No DNS/repository contact is possible in this namespace. Required packages
  # were signed and installed during image mode; missing packages fail closed.
  install -d -m 0755 /run/j716s
  exec env J716S_OFFLINE_NAMESPACE=1 unshare --mount --net --propagation private bash "$0" __provision "$2"
}

check() {
  say 'Read-only check; no sudo, key import, DNS changes, transactions or service mutations.'
  [[ $(uname -m) == aarch64 ]] || die 'Actual J716s check requires aarch64.'
  check_keys
  python "$policy" boot
  pacman -Q omarchy-dev omarchy-settings-dev omarchy-mac hyprland quickshell uwsm sddm
  findmnt -nro SOURCE,FSTYPE,UUID --target /
  findmnt -nro SOURCE,FSTYPE,PARTUUID --mountpoint /boot
  say 'GPU, display, audio and physical boot success are not asserted by this check.'
}

case ${1:-check} in
  check) [[ $# -le 1 ]] || die 'Usage: check'; check ;;
  image) [[ $# == 1 ]] || die 'Usage: image'; image ;;
  provision) shift; provision "$@" ;;
  __provision) [[ $# == 2 ]] || die 'Invalid internal operation'; provision_inner "$2" ;;
  *) die 'Usage: m5-omarchy-j716s.sh [check | image | provision --user USER]' ;;
esac
