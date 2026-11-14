#!/usr/bin/env python3
"""Build a secret-free ARM64 RAM desktop closure from a completed root image.

Uses host readelf only: no retained guest, chroot, mounts or target-disk writes.
The rebuilt archive still requires physical display/GPU qualification.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import uuid

PROJECT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source-root', type=Path, required=True)
parser.add_argument('--output', type=Path, default=PROJECT / 'work/aura-audit/ram-desktop')
parser.add_argument('--readelf', default='readelf')
args = parser.parse_args()
SOURCE = args.source_root.resolve(strict=True)
OUT = args.output.resolve()
if not SOURCE.is_dir() or OUT.is_relative_to(SOURCE):
    raise ValueError('Source must be an unpacked completed root; output must be outside it')
DEST = OUT / 'probe-root'
OUT.mkdir(parents=True, exist_ok=False)
DEST.mkdir(exist_ok=False)
copied = set()
elfs = set()

def canonical(name):
    pending = list(PurePosixPath('/' + name).parts[1:])
    resolved = []
    hops = 0
    while pending:
        part = pending.pop(0)
        if part in ('', '.'):
            continue
        if part == '..':
            if not resolved:
                raise RuntimeError('Path escapes root: ' + name)
            resolved.pop()
            continue
        candidate = SOURCE.joinpath(*resolved, part)
        if candidate.is_symlink():
            hops += 1
            if hops > 40:
                raise RuntimeError('Symlink cycle: ' + name)
            target = os.readlink(candidate)
            if target.startswith('/'):
                resolved = []
            pending = list(PurePosixPath(target).parts[1:] if target.startswith('/') else PurePosixPath(target).parts) + pending
        else:
            resolved.append(part)
    return '/'.join(resolved)

def add(name):
    name = str(PurePosixPath(name)).lstrip('/')
    if name in copied:
        return
    src = SOURCE / name
    dst = DEST / name
    # Parent symlinks must retain their root-relative meaning in the probe.
    parent = str(PurePosixPath(name).parent)
    if parent != '.':
        add_parent(parent)
    copied.add(name)
    if src.is_symlink():
        dst.symlink_to(os.readlink(src))
        add(canonical(name))
        return
    if src.is_dir():
        dst.mkdir(exist_ok=True)
        return
    if not src.is_file():
        raise RuntimeError('Missing dependency: ' + name)
    shutil.copy2(src, dst)
    with src.open('rb') as stream:
        is_elf = stream.read(4) == b'\x7fELF'
    if not is_elf or name in elfs:
        return
    elfs.add(name)
    details = subprocess.check_output([args.readelf, '-hW', '-lW', '-dW', str(src)], text=True)
    if not re.search(r'Machine:\s+AArch64', details):
        raise RuntimeError('Not an ARM64 ELF: ' + name)
    for interp in re.findall(r'Requesting program interpreter: ([^\]]+)', details):
        add(interp)
    origin = str(PurePosixPath(name).parent)
    search = ['opt/mesa-j716s/lib']
    for paths in re.findall(r'\((?:RUNPATH|RPATH)\).*\[([^\]]+)\]', details):
        search.extend(p.replace('${ORIGIN}', origin).replace('$ORIGIN', origin).lstrip('/') for p in paths.split(':'))
    search.extend([origin, 'usr/lib'])
    for library in re.findall(r'\(NEEDED\).*\[([^\]]+)\]', details):
        for directory in search:
            candidate = directory + '/' + library
            if (SOURCE / canonical(candidate)).is_file():
                add(candidate)
                break
        else:
            raise RuntimeError('Unresolved ' + library + ' from ' + name)

def add_parent(name):
    if name == '.' or name in copied:
        return
    src = SOURCE / name
    if src.is_symlink():
        add(name)
        return
    add_parent(str(PurePosixPath(name).parent))
    (DEST / name).mkdir(exist_ok=True)
    copied.add(name)

def tree(name, exclude=()):
    add(name)
    for directory, dirs, files in os.walk(SOURCE / name, followlinks=False):
        dirs[:] = [d for d in dirs if d not in exclude]
        if name in python_dirs:
            files = [f for f in files if not f.startswith(('_tkinter.', '_test'))]
        for child in dirs + files:
            add(str((Path(directory) / child).relative_to(SOURCE)))

python_dirs = [str(path.relative_to(SOURCE)) for path in SOURCE.glob('usr/lib/python3.*') if path.is_dir()]
if len(python_dirs) != 1:
    raise RuntimeError('Expected one complete Python standard-library directory')
for name in ['bin', 'sbin', 'lib', 'lib64']:
    if (SOURCE / name).exists():
        add(name)
for name in ['Hyprland', 'hyprctl', 'foot', 'dbus-daemon', 'dbus-run-session',
             'seatd', 'seatd-launch', 'vulkaninfo', 'vkcube', 'modetest', 'iw',
             'wpa_supplicant', 'wpa_cli', 'python', 'python3', 'bash', 'sh',
             'env', 'setpriv', 'udevadm', 'fsck.fat', 'fc-match', 'grim']:
    add('usr/bin/' + name)
add('usr/lib/systemd/systemd-udevd')
for name in ['libEGL.so.1', 'libGLX.so.0', 'libOpenGL.so.0', 'libGLdispatch.so.0',
             'libvulkan.so.1', 'libgbm.so.1', 'libnss_files.so.2', 'libnss_dns.so.2']:
    add('usr/lib/' + name)
for name in ['opt/mesa-j716s/lib', 'opt/mesa-j716s/share', 'usr/share/xkeyboard-config-2',
             'usr/share/hypr', 'etc/fonts', 'usr/lib/udev/rules.d',
             'usr/share/dbus-1', *python_dirs]:
    tree(name, exclude=('site-packages', '__pycache__', 'test', 'tests', 'Tools', 'pkgconfig'))
for name in ['usr/lib/udev/hwdb.bin', 'etc/udev/udev.conf']:
    if (SOURCE / name).is_file():
        add(name)
add('usr/share/fonts/TTF/JetBrainsMonoNerdFont-Regular.ttf')
add('usr/local/bin/j716s-gpu-preflight')
add('opt/mesa-j716s/MESA_COMMIT')
for name in ['dev', 'proc', 'sys', 'run', 'tmp', 'home/probe', 'root', 'etc/dbus-1', 'etc/xdg', 'var/cache/fontconfig']:
    (DEST / name).mkdir(parents=True, exist_ok=True)
(DEST / 'tmp').chmod(0o1777)
(DEST / 'etc/passwd').write_text('root:x:0:0:RAM probe:/root:/bin/bash\nprobe:x:1000:1000:RAM graphics probe:/home/probe:/bin/bash\nnobody:x:65534:65534:nobody:/:/usr/bin/nologin\n')
(DEST / 'etc/group').write_text('root:x:0:\nprobe:x:1000:\nvideo:x:986:probe\nrender:x:989:probe\ninput:x:994:probe\ntty:x:5:\ndisk:x:6:\nnobody:x:65534:\n')
(DEST / 'etc/nsswitch.conf').write_text('passwd: files\ngroup: files\nhosts: files dns\n')
(DEST / 'etc/machine-id').write_text(uuid.uuid4().hex + '\n')
config = '''hl.monitor({ output = "", mode = "3456x2234@120", position = "auto", scale = 2 })
hl.config({ input = { kb_layout = "us", touchpad = { natural_scroll = false, clickfinger_behavior = true } }, animations = { enabled = false }, decoration = { blur = { enabled = false } } })
hl.bind("SUPER + Return", hl.dsp.exec_cmd("foot"))
hl.bind("SUPER + Q", hl.dsp.window.close())
hl.bind("SUPER + SHIFT + E", hl.dsp.exit())
hl.on("hyprland.start", function() hl.exec_cmd("foot --title=J716s-RAM-probe") end)
'''
(DEST / 'home/probe/hyprland.lua').write_text(config)
(DEST / 'usr/local/bin/j716s-start-desktop').write_text('''#!/usr/bin/bash
set -euo pipefail
export HOME=/home/probe XDG_RUNTIME_DIR=/run/user/1000 XDG_SESSION_TYPE=wayland
export LIBSEAT_BACKEND=seatd SEATD_SOCK=/run/seatd.sock
J716S_GPU_ENV=$(/usr/local/bin/j716s-gpu-preflight --session-env --allow-dcp)
eval "$J716S_GPU_ENV"
exec /usr/bin/dbus-run-session /usr/bin/Hyprland -c /home/probe/hyprland.lua
''')
(DEST / 'usr/local/bin/j716s-start-desktop').chmod(0o755)
(DEST / 'run/user/1000').mkdir(parents=True, mode=0o700)
archive = OUT / 'desktop-probe.tar.gz'
with tarfile.open(archive, 'x:gz', compresslevel=6, dereference=False) as stream:
    def public_ram_metadata(member):
        member.uid = member.gid = 1000 if member.name in ('./home/probe', './home/probe/hyprland.lua', './run/user/1000') else 0
        member.uname = member.gname = ''
        member.mtime = 0
        return member
    stream.add(DEST, arcname='.', recursive=True, filter=public_ram_metadata)
receipt = {'schema': 1, 'kind': 'RAM-only graphical userspace dependency closure',
           'size': archive.stat().st_size,
           'sha256': hashlib.file_digest(archive.open('rb'), 'sha256').hexdigest(),
           'copied_paths': len(copied), 'elf_objects': len(elfs),
           'persistent_accounts_or_secrets_copied': False, 'hardware_rendering_verified': False,
           'qualification': 'fresh closure requires physical native GPU/DCP/display qualification',
           'launch': 'start-desktop from the rescue shell'}
(OUT / 'probe-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
print(json.dumps(receipt, indent=2), flush=True)
