#!/usr/bin/bash
set -eu
[[ $(id -u) == 0 && $(tty) == /dev/tty1 ]]
printf '\nJ716s root/ESP/deployment validation failed. Desktop startup is blocked.\n'
printf 'This recovery root shell is available on the physical tty1 only; no network login is enabled.\n'
printf 'Inspect: journalctl -b -u j716s-boot-bind.service\n'
printf 'Do not bypass identity validation. Repair the receipt/device/image mismatch, then run:\n'
printf '  systemctl restart j716s-boot-bind.service\n'
printf '  systemctl restart j716s-firstboot.service\n'
exec /usr/bin/bash --noprofile --norc -i
