#!/usr/bin/python
"""Fail-closed package/boot policy for the custom J716s ext4 image."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

STATE = Path('/var/lib/j716s')
# Only linux-api-headers and userspace tools are allowed; exact J716s firmware
# is deployed separately and generic linux-firmware packages cannot replace it.
DENY = re.compile(r'^(?:linux(?:$|-(?!api-headers$|tools(?:-|$)).*)|m1n1.*|grub.*|limine.*|u-?boot.*|uboot.*|dracut.*|systemd-boot.*|omarchy-mac-boot|asahi-scripts|asahi-fwextract|asahi-fwupdate|kernel-modules-hook|snapper)$')
DANGEROUS = re.compile(r'(?<![\w-])(?:mkinitcpio|dracut|kernel-install|update-m1n1|limine(?:-update|-install)?|grub-install|grub-mkconfig|bootctl|uboot-install|tune2fs|linux-modules-cleanup)(?![\w-])|(?:\bboot/|/?(?:usr/)?lib/modules|/?opt/mesa-j716s)|(?:randomi[sz].{0,30}(?:uuid|root)|(?:uuid|root).{0,30}randomi[sz])', re.I)
SCRIPT_DANGER = re.compile(r'(?:^\s*|[;|&]\s*|\b(?:exec|command|sudo)\s+)(?:(?:/usr)?/(?:s?bin)/)?(?:mkinitcpio|dracut|kernel-install|update-m1n1|limine(?:-update|-install)?|grub-install|grub-mkconfig|bootctl|uboot-install|tune2fs|linux-modules-cleanup)(?:\s|$)|/boot(?:/|\s)|/usr/lib/modules|/lib/modules|/opt/mesa-j716s', re.I | re.M)
MASKED_HOOKS = {'60-mkinitcpio-remove.hook', '90-mkinitcpio-install.hook', '90-linux-modules-cleanup.hook', '60-linux-modules-remove.hook', '90-linux-modules-install.hook'}


def fail(message):
    raise SystemExit('J716s policy: ' + message)


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def load(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.new')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    temporary.replace(path)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def protected(path):
    path = path.removeprefix('./').strip('/')
    return path == 'boot' or path.startswith(('boot/', 'usr/lib/modules/', 'lib/modules/', 'opt/mesa-j716s/', 'etc/j716s/', 'usr/share/j716s/', 'etc/pacman.d/hooks/00-j716s-', 'usr/local/lib/j716s/')) or path in ('opt/mesa-j716s', 'usr/local/lib/j716s')


def metadata(path):
    result = {}
    for line in command('bsdtar', '-xOf', str(path), '.PKGINFO').splitlines():
        if ' = ' in line:
            key, value = line.split(' = ', 1)
            result.setdefault(key, []).append(value)
    return result


def active_lines(text):
    return '\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('#'))


def hook_danger(text):
    # Targets may mention boot ownership: that is a reason to block execution,
    # even when the Exec target is an innocuously named helper.
    return bool(DANGEROUS.search(active_lines(text)))


def mask_hook(name):
    target = Path('/etc/pacman.d/hooks') / name
    if name == '00-j716s-ownership-guard.hook':
        fail('attempt to mask the ownership guard')
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() and os.readlink(target) == '/dev/null':
        return
    if target.exists() or target.is_symlink():
        fail('cannot overwrite administrator hook ' + str(target))
    target.symlink_to('/dev/null')


def scan_archive(path, mask=False):
    info = metadata(path)
    name = info['pkgname'][0]
    if DENY.fullmatch(name):
        fail('boot/ext4-incompatible package denied: ' + name)
    entries = command('bsdtar', '-tf', str(path)).splitlines()
    for entry in entries:
        rel = entry.removeprefix('./').rstrip('/')
        if protected(rel) and not entry.endswith('/'):
            fail(f'{name} claims protected path {entry}')
        if '/libalpm/hooks/' in '/' + rel and rel.endswith('.hook'):
            text = command('bsdtar', '-xOf', str(path), entry)
            if hook_danger(text):
                if mask:
                    mask_hook(Path(rel).name)
                target = Path('/etc/pacman.d/hooks') / Path(rel).name
                if not (target.is_symlink() and os.readlink(target) == '/dev/null'):
                    fail(f'dangerous hook {rel} in {name} is not masked')
        if rel == '.INSTALL':
            text = active_lines(command('bsdtar', '-xOf', str(path), entry))
            if SCRIPT_DANGER.search(text):
                fail(f'{name} scriptlet may mutate the boot/module/UUID contract')
    return info


def installed_records():
    for directory in Path('/var/lib/pacman/local').glob('*'):
        desc = directory / 'desc'
        if not desc.is_file():
            continue
        sections = {}
        key = None
        for line in desc.read_text().splitlines():
            if line.startswith('%'):
                key = line.strip('%')
                sections[key] = []
            elif line and key:
                sections[key].append(line)
        if 'NAME' in sections:
            yield sections['NAME'][0], sections.get('VERSION', [''])[0], directory


def audit_installed():
    for name, _, directory in installed_records():
        if DENY.fullmatch(name):
            fail('forbidden package installed: ' + name)
        files = directory / 'files'
        if files.exists():
            for path in files.read_text().splitlines():
                if protected(path) and not path.endswith('/'):
                    fail(f'{name} owns protected path {path}')
    for directory in ('/usr/share/libalpm/hooks', '/etc/pacman.d/hooks'):
        for hook in Path(directory).glob('*.hook'):
            if hook.name == '00-j716s-ownership-guard.hook':
                continue
            if hook.is_file() and hook_danger(hook.read_text()):
                override = Path('/etc/pacman.d/hooks') / hook.name
                if not (override.is_symlink() and os.readlink(override) == '/dev/null'):
                    fail('unmasked boot-mutating hook: ' + str(hook))


def configure():
    for name in MASKED_HOOKS:
        mask_hook(name)
    for directory in ('/usr/share/libalpm/hooks', '/etc/pacman.d/hooks'):
        for hook in Path(directory).glob('*.hook'):
            if hook.name != '00-j716s-ownership-guard.hook' and hook.is_file() and hook_danger(hook.read_text()):
                mask_hook(hook.name)
    config = Path('/etc/pacman.conf')
    text = config.read_text()
    text = re.sub(r'(?m)^\s*(?:IgnorePkg|LocalFileSigLevel)\s*=.*\n?', '', text)
    ignore = 'linux linux-aarch64* linux-asahi* linux-aurora* linux-omarchy* linux-firmware* m1n1* grub* limine* uboot* u-boot* dracut* systemd-boot* omarchy-mac-boot asahi-scripts kernel-modules-hook snapper'
    text = text.replace('[options]', '[options]\nIgnorePkg = ' + ignore + '\nLocalFileSigLevel = Required', 1)
    if text.count('[options]') != 1:
        fail('invalid official pacman options section')
    if not re.search(r'(?m)^SigLevel\s*=\s*Required(?:\s|$)', text):
        fail('package signature requirement missing')
    config.write_text(text)


def transaction(remove=False):
    if Path('/run/j716s/offline-provision').exists():
        fail('package transactions are forbidden during offline owner provisioning')
    names = set(sys.stdin.read().split())
    if not names:
        fail('empty transaction target list')
    records = {name: directory for name, _, directory in installed_records()}
    for name in names:
        if DENY.fullmatch(name) or remove and name in ('pacman', 'python', 'libarchive'):
            fail('forbidden transaction target: ' + name)
        if name in records:
            files = records[name] / 'files'
            if files.exists() and any(protected(p) and not p.endswith('/') for p in files.read_text().splitlines()):
                fail('transaction changes protected owner: ' + name)
        if remove:
            continue
        cached = list(Path('/var/cache/pacman/pkg').glob(name + '-*.pkg.tar.*'))
        found = False
        for path in cached:
            if path.name.endswith(('.sig', '.part')):
                continue
            info = metadata(path)
            if info.get('pkgname') == [name]:
                scan_archive(path)
                found = True
        # Install/upgrade downloads before PreTransaction. Inspect the actual
        # archive even for previously installed packages; absence fails closed.
        if not found:
            fail('cannot inspect downloaded package: ' + name)
    audit_installed()


def boot_guard():
    binding = load('/etc/j716s/boot.json')
    identity = load('/usr/share/j716s/image-identity.json')
    if binding.get('schema') != 1 or binding.get('kind') != 'j716s-omarchy-deployment' or binding.get('board') != 'j716s':
        fail('missing deployment binding')
    if binding.get('firmware_build') != '26C5035r':
        fail('unexpected firmware build')
    compatible = Path('/proc/device-tree/compatible').read_bytes().split(b'\0')
    if b'apple,j716s' not in compatible:
        fail('running device tree is not apple,j716s')
    release = Path('/etc/j716s/kernel.release').read_text().strip()
    if command('uname', '-r') != release or binding.get('kernel_release') != release:
        fail('running kernel does not match deployment')
    for key in ('board', 'kernel_release', 'firmware_build', 'filesystem_uuid'):
        if identity.get(key) != binding.get(key):
            fail('image identity mismatch: ' + key)
    if digest('/usr/share/j716s/image-identity.json') != binding.get('image_identity_sha256'):
        fail('image identity hash mismatch')
    root_manifest = load('/boot/aurora/root-manifest.json')
    for key in ('kernel_build_manifest_sha256', 'loader_build_manifest_sha256'):
        if not binding.get(key) or binding[key] != identity.get(key) or binding[key] != root_manifest.get(key):
            fail('fresh build crossbinding mismatch: ' + key)
    if identity.get('kernel_artifacts') != root_manifest.get('kernel_artifacts'):
        fail('image kernel artifact inventory differs')
    for name, artifact in identity['kernel_artifacts'].items():
        if name == 'modules.tar.gz':
            continue
        path = Path('/usr/lib/j716s/kernel') / name
        if not path.is_file() or path.is_symlink() or path.stat().st_size != artifact['size_bytes'] or digest(path) != artifact['sha256']:
            fail('image kernel artifact changed: ' + name)
    if identity.get('graphics_helpers') != root_manifest.get('graphics_helpers'):
        fail('image graphics helper inventory differs')
    for installed, artifact in identity['graphics_helpers'].items():
        if installed not in ('usr/local/bin/j716s-gpu-preflight', 'usr/local/libexec/j716s-session-env'):
            fail('unexpected graphics helper')
        path = Path('/') / installed
        if not path.is_file() or path.is_symlink() or path.stat().st_size != artifact['size_bytes'] or digest(path) != artifact['sha256']:
            fail('image graphics helper changed: ' + installed)
    root = command('findmnt', '-fnro', 'SOURCE', '--target', '/').split('[')[0]
    esp = command('findmnt', '-fnro', 'SOURCE', '--mountpoint', '/boot').split('[')[0]
    if command('findmnt', '-fnro', 'FSTYPE', '--target', '/') != 'ext4':
        fail('persistent root must be ext4')
    for key, device, field in (('linux_partition_uuid', root, 'PARTUUID'), ('esp_partition_uuid', esp, 'PARTUUID'), ('filesystem_uuid', root, 'UUID')):
        want = str(uuid.UUID(binding[key]))
        if command('blkid', '-s', field, '-o', 'value', device).lower() != want:
            fail('mounted device mismatch: ' + key)
    if command('blkid', '-p', '-s', 'PART_ENTRY_TYPE', '-o', 'value', root).lower() != '0fc63daf-8483-4772-8e79-3d69d8477de4':
        fail('root partition is not Linux filesystem data')
    if command('blkid', '-p', '-s', 'PART_ENTRY_TYPE', '-o', 'value', esp).lower() != 'c12a7328-f81f-11d2-ba4b-00a0c93ec93b':
        fail('boot partition is not an ESP')
    esp_binding = load('/boot/aurora/deployment.json')
    for key, value in esp_binding.items():
        if binding.get(key) != value:
            fail('root/ESP receipt disagreement: ' + key)
    for path_key, hash_key in (('root_payload_path', 'root_payload_sha256'), ('rescue_path', 'rescue_sha256')):
        relative = Path(binding[path_key])
        if relative.is_absolute() or '..' in relative.parts or relative.parts[0] != 'aurora':
            fail('invalid payload path')
        if digest(Path('/boot') / relative) != binding[hash_key]:
            fail('payload changed: ' + path_key)
    dependencies = binding.get('payload_files', [])
    if len(dependencies) != 6 or len({entry['path'] for entry in dependencies}) != 6:
        fail('boot dependency inventory differs')
    expected_paths = {'aurora/root/Image.gz', 'aurora/root/t6050-j716s.dtb', 'aurora/root/initramfs.cpio.gz',
                      'aurora/rescue/Image.gz', 'aurora/rescue/t6050-j716s.dtb', 'aurora/rescue/initramfs.cpio.gz'}
    if {entry['path'] for entry in dependencies} != expected_paths:
        fail('boot dependency paths differ')
    for entry in dependencies:
        relative = Path(entry['path'])
        if relative.is_absolute() or '..' in relative.parts or relative.parts[:2] not in (('aurora', 'root'), ('aurora', 'rescue')):
            fail('invalid boot dependency path')
        path = Path('/boot') / relative
        if path.resolve() != path or path.is_symlink() or path.stat().st_size != entry['size'] or digest(path) != entry['sha256']:
            fail('boot dependency changed: ' + str(relative))
    if digest('/boot/aurora/root-manifest.json') != binding['root_manifest_sha256']:
        fail('root manifest receipt changed')
    if binding.get('stage2_path') != 'aurora/boot.bin' or digest('/boot/aurora/boot.bin') != binding['root_payload_sha256']:
        fail('stable active Stage 2 changed')
    if digest('/boot/aurora/rescue-manifest.json') != binding.get('rescue_manifest_sha256'):
        fail('rescue manifest changed')
    rescue = load('/boot/aurora/rescue-manifest.json')
    if (rescue.get('sha256') != binding['rescue_sha256'] or rescue.get('esp_partuuid') != binding['esp_partition_uuid']
            or any(rescue.get(key) != binding[key] for key in ('kernel_build_manifest_sha256', 'loader_build_manifest_sha256'))):
        fail('rescue fresh-build crossbinding differs')
    if [entry for entry in dependencies if entry['path'].startswith('aurora/rescue/')] != [
            {key: entry[key] for key in ('path', 'sha256', 'size')} for entry in rescue['files']]:
        fail('rescue dependency crossbinding differs')
    audit_installed()


def manifest(output):
    selected, excluded = set(), {}
    for name in ('omarchy-base', 'omarchy-aarch64', 'omarchy-aarch64-apple'):
        path = Path('/usr/share/omarchy/install') / (name + '.packages')
        if not path.is_file():
            fail('missing official package manifest: ' + str(path))
        for line in path.read_text().splitlines():
            line = line.split('#', 1)[0].strip()
            if not line:
                continue
            if len(line.split()) != 1:
                fail('invalid package manifest line: ' + line)
            if DENY.fullmatch(line):
                excluded[line] = 'Custom kernel/loader ownership or ext4-incompatible snapshot/module cleanup'
            else:
                selected.add(line)
    if len(selected) < 100:
        fail('incomplete official desktop manifest')
    Path(output).write_text('\n'.join(sorted(selected)) + '\n')
    save(STATE / 'manifest-exclusions.json', excluded)


def receipt():
    audit_installed()
    versions = {name: version for name, version, _ in installed_records()}
    required = Path('/var/lib/j716s/manifest-packages.txt').read_text().splitlines()
    missing = [name for name in required if name not in versions and subprocess.run(['pacman', '-T', name], stdout=subprocess.DEVNULL).returncode]
    if missing:
        fail('required official desktop packages absent: ' + ', '.join(missing))
    policies = Path('/usr/local/lib/j716s/policies')
    keys = Path('/usr/local/lib/j716s/keys')
    save(STATE / 'omarchy-image.json', dict(schema=1, board='j716s', channel='edge', packages=versions, manifest_packages=required, exclusions=load(STATE / 'manifest-exclusions.json'), template_sources=load(policies / 'sources.json'), signing_key_sources=load(keys / 'sources.json'), signature_policy='Required for repository and local packages; official ALARM keyring plus fingerprint-pinned Omarchy/asahi-alarm signing keys'))


def verify_receipt():
    baked = load(STATE / 'omarchy-image.json')
    if baked.get('schema') != 1 or baked.get('board') != 'j716s' or baked.get('channel') != 'edge':
        fail('invalid baked desktop receipt')
    current = {name: version for name, version, _ in installed_records()}
    for name, version in baked['packages'].items():
        if current.get(name) != version:
            fail('baked package version changed or missing: ' + name)
    audit_installed()


def fresh_owner(user):
    fields = command('getent', 'passwd', user).split(':')
    if fields[0] == 'root' or int(fields[2]) < 1000 or fields[5] != '/home/' + user:
        fail('owner must be a fresh ordinary /home account')
    home = Path(fields[5])
    skel = Path('/etc/skel')
    for path in home.rglob('*'):
        relative = path.relative_to(home)
        original = skel / relative
        if original.is_symlink() and path.is_symlink() and os.readlink(path) == os.readlink(original):
            continue
        if path.is_dir() and original.is_dir():
            continue
        if path.is_file() and original.is_file() and digest(path) == digest(original):
            continue
        # Existing arbitrary dotfiles are never overwritten.
        fail('owner home is not a fresh /etc/skel copy: ' + str(relative))
    marker = STATE / 'owner-provisioning.json'
    if marker.exists():
        fail('owner provisioning already started; refusing to overwrite user files')
    save(marker, dict(schema=1, user=user, uid=int(fields[2]), home=str(home), status='started'))


if __name__ == '__main__':
    mode = sys.argv[1]
    if mode == 'configure':
        configure()
    elif mode == 'transaction':
        transaction()
    elif mode == 'transaction-remove':
        transaction(remove=True)
    elif mode == 'scan-cache':
        for argument in sys.argv[2:]:
            scan_archive(Path(argument), mask=True)
    elif mode == 'audit':
        audit_installed()
    elif mode == 'boot':
        boot_guard()
    elif mode == 'manifest':
        manifest(sys.argv[2])
    elif mode == 'receipt':
        receipt()
    elif mode == 'verify-receipt':
        verify_receipt()
    elif mode == 'fresh-owner':
        fresh_owner(sys.argv[2])
    else:
        fail('unknown policy operation')
