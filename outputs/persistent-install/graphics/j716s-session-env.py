#!/usr/bin/env python3
"""Install managed native-Mesa UWSM hooks only after the real owner exists."""
import argparse
import os
from pathlib import Path
import pwd
import sys
import runpy

BEGIN = '# BEGIN J716S PRIVATE MESA'
END = '# END J716S PRIVATE MESA'
ENV = '''# BEGIN J716S PRIVATE MESA
# Actual GPU preflight precedes the Hyprland session; no software-as-GPU fallback.
J716S_GPU_ENV=$(/usr/local/bin/j716s-gpu-preflight --session-env --allow-dcp) || exit 1
eval "$J716S_GPU_ENV"
unset J716S_GPU_ENV
# END J716S PRIVATE MESA
'''


def directory(path, uid, gid):
    if path.is_symlink():
        raise RuntimeError('Refusing symlink directory: ' + str(path))
    path.mkdir(mode=0o700, exist_ok=True)
    if not path.is_dir():
        raise RuntimeError('Not a directory: ' + str(path))
    os.chown(path, uid, gid)


def replace_managed(path, body, uid, gid):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RuntimeError('Refusing non-regular user environment file: ' + str(path))
    content = path.read_text() if path.exists() else ''
    if BEGIN in content or END in content:
        if content.count(BEGIN) != 1 or content.count(END) != 1:
            raise RuntimeError('Malformed J716S managed session block: ' + str(path))
        start = content.index(BEGIN); finish = content.index(END, start) + len(END)
        if finish < len(content) and content[finish] == '\n':
            finish += 1
        content = content[:start] + content[finish:]
    if body:
        content = content.rstrip('\n') + ('\n\n' if content else '') + body
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, 'w') as output:
        output.write(content)
        os.fchmod(output.fileno(), 0o600)
        os.fchown(output.fileno(), uid, gid)


def install_greeter():
    environment = runpy.run_path('/usr/local/bin/j716s-gpu-preflight')['private_environment']()
    if any(',' in value or '\n' in value for value in environment.values()):
        raise RuntimeError('Greeter environment values contain separators')
    target = Path('/etc/sddm.conf.d/20-j716s-private-mesa.conf')
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise RuntimeError('Unsafe greeter configuration')
    target.parent.mkdir(parents=True, exist_ok=True)
    content = '# J716s: native private Mesa, scoped to the SDDM greeter.\n[General]\nGreeterEnvironment='
    content += ','.join(key + '=' + value for key, value in sorted(environment.items())) + '\n'
    temporary = target.with_name(target.name + '.new')
    with temporary.open('x') as output:
        output.write(content)
        os.fchmod(output.fileno(), 0o644)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(target)



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--user')
    mode.add_argument('--greeter', action='store_true', help='bake or restore SDDM [General]GreeterEnvironment, without starting services')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError('Owner session installation requires root')
    if args.greeter:
        install_greeter()
        return
    owner = pwd.getpwnam(args.user)
    if owner.pw_uid < 1000 or owner.pw_shell.endswith(('nologin', 'false')):
        raise RuntimeError('A real non-root login owner is required')
    home = Path(owner.pw_dir)
    if not home.is_absolute() or not home.is_dir() or home.is_symlink():
        raise RuntimeError('Actual owner home directory is required')
    for artifact in ('/usr/local/bin/j716s-gpu-preflight', '/opt/mesa-j716s/MESA_COMMIT',
                     '/opt/mesa-j716s/share/j716s/build-receipt.json'):
        if not Path(artifact).is_file():
            raise RuntimeError('Missing completed private graphics build: ' + artifact)
    config = home / '.config'; uwsm = config / 'uwsm'
    directory(config, owner.pw_uid, owner.pw_gid)
    directory(uwsm, owner.pw_uid, owner.pw_gid)
    # Scope GPU preflight to Hyprland. General user services inherit the exports
    # UWSM imports from env-hyprland; non-graphical/login sessions remain usable.
    replace_managed(uwsm / 'env', '', owner.pw_uid, owner.pw_gid)
    replace_managed(uwsm / 'env-hyprland', ENV, owner.pw_uid, owner.pw_gid)
    print('Installed private Mesa UWSM Hyprland environment for ' + owner.pw_name)


if __name__ == '__main__':
    try:
        main()
    except (OSError, KeyError, ValueError, RuntimeError) as error:
        print('j716s-session-env: ' + str(error), file=sys.stderr)
        sys.exit(1)
