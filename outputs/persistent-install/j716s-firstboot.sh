#!/usr/bin/bash
# Physical tty1 is the only setup/recovery console; there are no preset credentials.
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/bin
export LC_ALL=C.UTF-8
state=/var/lib/j716s
mkdir -p "$state"
chmod 0700 "$state"
recovery() {
  local result=$?
  trap - ERR HUP INT TERM
  systemctl disable sddm.service >/dev/null 2>&1 || true
  printf '\nJ716s setup failed (status %s). The desktop was not enabled.\n' "$result" >&2
  printf 'Physical recovery shell on tty1 only. Inspect journalctl -b and /etc/j716s/boot.json.\n' >&2
  if [[ -f $state/owner-provisioning.json ]]; then
    printf 'Owner provisioning already started. Preserve the owner home; inspect the failed phase before any explicit reset. Blind service restart will not overwrite those files.\n' >&2
  else
    printf 'Repair the cause, exit this shell, then run: systemctl restart j716s-firstboot.service\n' >&2
  fi
  /usr/bin/bash --noprofile --norc -i </dev/tty1 >/dev/tty1 2>&1 || true
  exit 1
}
trap recovery ERR HUP INT TERM
[[ $(id -u) == 0 && $(tty) == /dev/tty1 ]]
[[ -f /etc/j716s/boot.json ]]
passwd -l root >/dev/null
printf '\nJ716s Omarchy first boot\n'
printf 'The running root, kernel and ESP are UUID-verified. No default account or password exists.\n'
if [[ -f $state/owner ]]; then
  owner=$(cat "$state/owner")
  [[ $owner =~ ^[a-z_][a-z0-9_-]{0,31}$ ]]
  [[ $(id -u "$owner") -ge 1000 ]]
  [[ $(passwd -S "$owner" | cut -d ' ' -f 2) == P ]]
else
  while true; do
    read -r -p 'Choose your local owner username: ' owner
    [[ $owner =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || { printf 'Use 1–32 lowercase letters/digits/underscore/hyphen; start with a letter or underscore.\n'; continue; }
    if getent passwd "$owner" >/dev/null; then
      printf 'That account already exists. Choose another username.\n'
      continue
    fi
    useradd --create-home --user-group --groups wheel --shell /bin/bash "$owner"
    printf 'Set a nonempty password for %s (nothing is echoed):\n' "$owner"
    until passwd "$owner"; do printf 'Password setup failed; try again.\n'; done
    [[ $(passwd -S "$owner" | cut -d ' ' -f 2) == P ]]
    printf '%s\n' "$owner" >"$state/owner"
    chmod 0600 "$state/owner"
    break
  done
fi
printf '%%wheel ALL=(ALL:ALL) ALL\n' >/etc/sudoers.d/10-j716s-owner
chmod 0440 /etc/sudoers.d/10-j716s-owner
visudo -c >/dev/null
/usr/local/lib/j716s/m5-omarchy-j716s.sh provision --user "$owner"
# Stock system phases may replace SDDM configuration; restore the verified
# greeter-scoped environment before allowing the graphical target to start.
/usr/local/bin/j716s-gpu-preflight --allow-dcp
/usr/local/libexec/j716s-session-env --greeter
# Provisioning may enable the manager; the gate still requires all provisioning
# and this final security check to have succeeded before starting it.
passwd -l root >/dev/null
python - <<'PY'
from pathlib import Path
import subprocess
for path in [Path('/etc/sudoers'), *Path('/etc/sudoers.d').glob('*')]:
    if path.is_file():
        active = '\n'.join(line for line in path.read_text().splitlines() if not line.lstrip().startswith('#'))
        if 'NOPASSWD' in active or '!authenticate' in active:
            raise SystemExit('Passwordless sudo policy remains: ' + str(path))
for path in Path('/etc/sddm.conf.d').glob('*.conf'):
    section = ''
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith('['):
            section = line.lower()
        if section == '[autologin]' and line.lower().startswith('user=') and line.split('=', 1)[1].strip():
            raise SystemExit('SDDM autologin policy remains: ' + str(path))
if Path('/etc/sddm.conf').exists():
    section = ''
    for line in Path('/etc/sddm.conf').read_text().splitlines():
        line = line.strip()
        if line.startswith('['):
            section = line.lower()
        if section == '[autologin]' and line.lower().startswith('user=') and line.split('=', 1)[1].strip():
            raise SystemExit('SDDM autologin is enabled')
subprocess.run(['visudo', '-c'], check=True)
PY
# Seed the remembered login user/session, not autologin. The stock greeter
# otherwise has no owner to pass to PAM on a fresh ext4 install.
install -d -m 0755 -o sddm -g sddm /var/lib/sddm
printf '[Last]\nSession=omarchy.desktop\nUser=%s\n' "$owner" >/var/lib/sddm/state.conf
chown sddm:sddm /var/lib/sddm/state.conf
systemctl enable sddm.service
systemctl set-default graphical.target
printf 'Owner and offline Omarchy provisioning completed.\n' >"$state/firstboot-complete.new"
chmod 0600 "$state/firstboot-complete.new"
mv "$state/firstboot-complete.new" "$state/firstboot-complete"
systemctl enable getty@tty1.service
systemctl --no-block start graphical.target
printf '\nSetup complete. Log in through SDDM with your new password.\n'
